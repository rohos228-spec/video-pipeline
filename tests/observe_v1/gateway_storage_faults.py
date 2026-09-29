"""Controlled offline SQLite, ACK and drain barriers for the exact pilot Handler."""
import importlib.util
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import httpx

ROOT = Path('/results')
SOURCE = Path('/observe-source')
ROOT.mkdir(exist_ok=True)
historical = Path('/historical-ledger/decision-gateway.sqlite')
with sqlite3.connect(f'file:{historical}?mode=ro&immutable=1', uri=True) as archive:
    archive_rows = archive.execute('SELECT ordinal,state,event_count,upstream_complete,downstream_complete,credits,terminal_body_path FROM requests ORDER BY ordinal').fetchall()
    archive_batches = archive.execute('SELECT request_ordinal,COUNT(*),MAX(batch_index) FROM event_batches GROUP BY request_ordinal').fetchall()
assert len(archive_rows) == 2 and archive_rows[1][1] == 'reserved'
(ROOT / 'historical-ledger-summary.json').write_text(json.dumps({
    'rows': archive_rows, 'batches': archive_batches,
    'wal_copy_present': historical.with_name(historical.name + '-wal').exists(),
    'shm_copy_present': historical.with_name(historical.name + '-shm').exists(),
}, indent=2), encoding='utf-8')
staged = Path('/gateway-source')
staged.mkdir(exist_ok=True)
shutil.copy2(SOURCE / 'gateway.py', staged / 'gateway.py')
shutil.copy2(SOURCE / 'scripts/diagnostics/decision_tool_cycle_gateway.py', staged / 'decision_tool_cycle_gateway.py')
shutil.copy2(SOURCE / 'config/decision-tool-cycle.example.json', staged / 'pilot-config.json')
spec = importlib.util.spec_from_file_location('pilot_gateway', staged / 'decision_tool_cycle_gateway.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
g = pilot.base
g.LEDGER = ROOT / 'fault-ledger.sqlite'
g.SOCKET = ROOT / 'fault.sock'
g.BODY_ROOT = ROOT / 'fault-bodies'
g.AUDIT_FAILURE = ROOT / 'fault-audit.marker'
g.DRAIN_REQUEST = ROOT / 'fault-drain.marker'
g.recover_pending()
server = g.Server(str(g.SOCKET), pilot.Handler)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()


def ack(stage):
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(g.SOCKET)), base_url='http://localhost', timeout=10) as client:
        return client.post('/local/decision-ack', json={'response_id': 'resp_race', 'stage': stage}).status_code


def pending():
    with sqlite3.connect(f'file:{g.LEDGER}?mode=ro', uri=True) as db:
        return db.execute("SELECT COUNT(*) FROM requests WHERE state='reserved'").fetchone()[0]


try:
    first = g.reserve('POST', '/codex/v1/responses')
    blocker = sqlite3.connect(g.LEDGER, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')
    saved = []
    started = threading.Event()
    def write_under_contention():
        started.set()
        saved.append(g.save_batch(first, 1, 1, [{'type': 'response.output_text.delta', 'delta': 'x'}], 'x'))
    writer = threading.Thread(target=write_under_contention)
    writer.start()
    assert started.wait(2)
    time.sleep(.2)
    assert writer.is_alive() and pending() == 1
    blocker.execute('ROLLBACK')
    blocker.close()
    writer.join(5)
    assert not writer.is_alive() and len(saved) == 1
    g.finish(first, status=200, credits=0, complete=True, downstream_complete=True)

    second = g.reserve('POST', '/codex/v1/responses')
    terminal = {'type': 'response.completed', 'credits_consumed': 0.7,
                'response': {'id': 'resp_race', 'usage': {'input_tokens': 2, 'output_tokens': 1}}}
    _, info = g.save_batch(second, 1, 1, [terminal], '{}', terminal=True, terminal_at=time.time())
    assert pending() == 1
    ack_result = []
    ack_started = threading.Event()
    def early_ack():
        ack_started.set()
        ack_result.append(ack('received'))
    waiter = threading.Thread(target=early_ack)
    waiter.start()
    assert ack_started.wait(2)
    time.sleep(.15)
    assert waiter.is_alive() and not ack_result and pending() == 1
    g.finish(second, status=200, credits=.7, complete=True, downstream_complete=True,
             terminal_info=info)
    waiter.join(5)
    assert ack_result == [200]
    assert ack('accepted') == 200
    assert ack('received') == 200 and ack('accepted') == 200
    assert pending() == 0
    with sqlite3.connect(g.LEDGER) as db:
        assert db.execute('SELECT state,response_id,credits,usage_json,terminal_body_path,accumulated_body_path FROM requests WHERE ordinal=2').fetchone()[0:3] == ('complete', 'resp_race', .7)
        assert db.execute('SELECT COUNT(*),SUM(attempts) FROM ack_events').fetchone() == (2, 4)

    third = g.reserve('POST', '/codex/v1/responses')
    _, orphan_info = g.save_batch(third, 1, 1, [terminal], '{}', terminal=True, terminal_at=time.time())
    assert (g.BODY_ROOT / orphan_info['terminal_path']).exists()
    g.recover_pending()  # Simulated restart before the settlement commit.
    with sqlite3.connect(g.LEDGER) as db:
        assert db.execute('SELECT state,credits,response_id FROM requests WHERE ordinal=3').fetchone() == ('uncertain', None, None)
    assert pending() == 0

    g.DRAIN_REQUEST.touch()
    try:
        g.reserve('POST', '/codex/v1/responses')
    except RuntimeError as exc:
        assert 'degraded' in str(exc)
    else:
        raise AssertionError('drain marker allowed new model reservation')
    original_connect = g.connect
    def unavailable():
        raise sqlite3.OperationalError('synthetic_drain_ledger_unavailable')
    g.connect = unavailable
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(g.SOCKET)), base_url='http://localhost', timeout=10) as client:
            assert client.post('/local/gateway-stop').status_code == 503
    finally:
        g.connect = original_connect
    assert server_thread.is_alive()
    marker = [json.loads(line) for line in g.AUDIT_FAILURE.read_text().splitlines()]
    assert marker[-1]['operation'] == 'drain_pending'
    assert marker[-1]['exception_text'] == 'synthetic_drain_ledger_unavailable'

    # A new process opening a committed request must never charge it again.
    g.LEDGER = ROOT / 'after-commit.sqlite'
    g.AUDIT_FAILURE = ROOT / 'after-commit.marker'
    g.DRAIN_REQUEST = ROOT / 'after-commit-drain.marker'
    g.AUDIT_DEGRADED = False  # New isolated ledger/process state for post-commit restart case.
    g.recover_pending()
    fourth = g.reserve('POST', '/codex/v1/responses')
    _, committed_info = g.save_batch(fourth, 1, 1, [terminal], '{}', terminal=True, terminal_at=time.time())
    g.finish(fourth, status=200, credits=.7, complete=True, downstream_complete=True,
             terminal_info=committed_info)
    g.recover_pending()  # Simulated restart after commit.
    with sqlite3.connect(g.LEDGER) as db:
        assert db.execute('SELECT COUNT(*),SUM(credits) FROM requests WHERE state=?', ('complete',)).fetchone() == (1, .7)
    result = {'transient_contention_recovered': True, 'ack_waited_for_commit': True,
              'duplicate_acks_rows': 2, 'duplicate_acks_attempts': 4,
              'precommit_restart_uncertain': True, 'postcommit_restart_one_settlement': True,
              'drain_unavailable_rejected': True, 'real_api_calls': 0}
    (ROOT / 'fault-summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
finally:
    server.shutdown()
    server.server_close()
