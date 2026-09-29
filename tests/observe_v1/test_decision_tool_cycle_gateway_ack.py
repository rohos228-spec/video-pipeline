"""Exercise the exact online pilot Handler over its Unix socket, offline."""
import importlib.util
import json
import sqlite3
import threading
import shutil
from pathlib import Path

import httpx

Path('/results').mkdir(exist_ok=True)
Path('/gateway-source').mkdir(exist_ok=True)
shutil.copy2('/observe-source/gateway.py', '/gateway-source/gateway.py')
shutil.copy2('/observe-source/scripts/diagnostics/decision_tool_cycle_gateway.py', '/gateway-source/decision_tool_cycle_gateway.py')
shutil.copy2('/observe-source/config/decision-tool-cycle.example.json', '/gateway-source/pilot-config.json')

spec = importlib.util.spec_from_file_location('pilot_gateway', '/gateway-source/decision_tool_cycle_gateway.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
base = pilot.base
root = Path('/tmp/pilot-ack-test')
root.mkdir(exist_ok=True)
base.LEDGER = root / 'ledger.sqlite'
base.SOCKET = root / 'llm.sock'
base.BODY_ROOT = root / 'bodies'
with base.connect() as db:
    db.execute("INSERT INTO requests(ordinal,at,method,path,state,response_id,upstream_complete,downstream_complete) "
               "VALUES(1,1,'POST','/codex/v1/responses','complete','resp_this_run',1,1)")
server = base.Server(str(base.SOCKET), pilot.Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    def call(method, path, **kwargs):
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(base.SOCKET)), base_url='http://localhost') as client:
            return client.request(method, path, **kwargs)
    def ack(response_id, stage):
        return call('POST', '/local/decision-ack', json={'response_id': response_id, 'stage': stage})
    assert ack('resp_this_run', 'accepted').status_code == 409
    assert ack('resp_this_run', 'received').status_code == 200
    assert ack('resp_this_run', 'received').status_code == 200
    assert ack('resp_this_run', 'accepted').status_code == 200
    assert ack('resp_this_run', 'accepted').status_code == 200
    assert ack('resp_foreign', 'received').status_code == 409
    assert call('POST', '/local/decision-ack', json=[]).status_code == 409
    original_ack = base.acknowledge
    def storage_failure(*_args):
        raise sqlite3.OperationalError('synthetic_ack_storage_failure')
    base.acknowledge = storage_failure
    try:
        assert ack('resp_this_run', 'accepted').status_code == 409
    finally:
        base.acknowledge = original_ack
    assert call('POST', '/local/other').status_code == 403
    assert call('GET', '/codex/v1/responses/resp_this_run').status_code == 403
    with sqlite3.connect(base.LEDGER) as db:
        row = db.execute('SELECT COUNT(*),COUNT(application_received_at),COUNT(application_accepted_at) '
                         'FROM requests').fetchone()
        assert row == (1, 1, 1), row
        audit = db.execute('SELECT stage,attempts,accepted FROM ack_events '
                           'WHERE response_id=? ORDER BY stage', ('resp_this_run',)).fetchall()
        assert audit == [('accepted', 3, 1), ('received', 2, 1)], audit
        foreign = db.execute('SELECT accepted,reason FROM ack_events WHERE response_id=?',
                             ('resp_foreign',)).fetchone()
        assert foreign == (0, 'foreign_or_ambiguous_response'), foreign
        db.executemany("INSERT INTO requests(ordinal,at,method,path,state,response_id,upstream_complete) "
                       "VALUES(?,1,'POST','/codex/v1/responses','complete','resp_ambiguous',1)",
                       [(2,), (3,)])
    assert ack('resp_ambiguous', 'received').status_code == 409
    with sqlite3.connect(base.LEDGER) as db:
        assert db.execute('SELECT reason FROM ack_events WHERE response_id=?',
                          ('resp_ambiguous',)).fetchone() == ('foreign_or_ambiguous_response',)
        assert db.execute('SELECT COUNT(application_received_at) FROM requests '
                          'WHERE response_id=?', ('resp_ambiguous',)).fetchone() == (0,)
    print(json.dumps({'handler': 'decision_tool_cycle_gateway.Handler', 'local_acks': 4,
                      'foreign_rejected': True, 'ambiguous_rejected': True,
                      'provider_posts': 0, 'upstream_get': 0}))
finally:
    server.shutdown()
    server.server_close()
