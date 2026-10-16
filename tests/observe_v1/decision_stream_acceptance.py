import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path('/results')
SOURCE = Path('/observe-source')
DATA = ROOT / 'free-run-data'
CASE = os.environ.get('OBSERVE_STREAM_CASE', 'fast')

def save(name, value):
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

def context_from(body):
    decoder = json.JSONDecoder()
    def walk(value):
        if isinstance(value, str):
            for pos, ch in enumerate(value):
                if ch == '{':
                    try: obj, _ = decoder.raw_decode(value[pos:])
                    except ValueError: continue
                    if isinstance(obj, dict) and 'allowed_actions' in obj and 'context_version' in obj:
                        return obj
        elif isinstance(value, dict):
            for item in value.values():
                got = walk(item)
                if got: return got
        elif isinstance(value, list):
            for item in value:
                got = walk(item)
                if got: return got
        return None
    result = walk(body)
    assert result is not None
    return result

class Upstream(BaseHTTPRequestHandler):
    calls = 0
    emitted = 0
    first_at = None
    terminal_at = None
    def log_message(self, *_): pass
    def do_POST(self):
        assert self.path == '/codex/v1/responses'
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        ctx = context_from(body)
        type(self).calls += 1
        proposal = {'action': 'human_review', 'arguments': {}, 'evidence_ids': ctx['evidence_ids'][:1],
            'context_version': ctx['context_version'], 'explanation': 'Fixture: human assessment requested.'}
        answer = json.dumps(proposal, ensure_ascii=False)
        response = {'id': 'resp_fixture_stream', 'status': 'completed',
            'output': [{'content': [{'type': 'output_text', 'text': answer}]}],
            'usage': {'input_tokens': 100, 'output_tokens': 40, 'fixture': True}, 'credits_consumed': 0.01}
        events = [{'type': 'response.created', 'response': {'id': response['id']},
                   'authorization': 'Bearer synthetic-secret-for-redaction',
                   'url': 'https://example.test/path?key=synthetic-secret-for-redaction'}]
        chunks = 120 if CASE == 'slow' else 4
        width = max(1, (len(answer) + chunks - 1) // chunks)
        events.extend({'type': 'response.output_text.delta', 'delta': answer[i:i + width]}
            for i in range(0, len(answer), width))
        if CASE != 'timeout':
            events.extend([{'type': 'response.output_text.done', 'text': answer},
                {'type': 'response.completed', 'response': response}])
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        for event in events:
            if CASE == 'late_terminal' and event['type'] == 'response.output_text.done':
                time.sleep(3.5)
            raw = ('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode()
            if type(self).first_at is None: type(self).first_at = time.monotonic_ns()
            try:
                self.wfile.write(('%x\r\n' % len(raw)).encode() + raw + b'\r\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError): break
            type(self).emitted += 1
            if event['type'] == 'response.completed': type(self).terminal_at = time.monotonic_ns()
        if CASE == 'timeout': time.sleep(3.5)
        try: self.wfile.write(b'0\r\n\r\n'); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError): pass

def setup_gateway():
    spec = importlib.util.spec_from_file_location('diagnostic_gateway', '/observe-source/gateway.py')
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)
    gateway.KIE = 'http://127.0.0.1:18767'
    gateway.SOCKET = ROOT / 'llm.sock'
    gateway.LEDGER = ROOT / 'gateway.sqlite'
    gateway.MAX_REQUESTS = 1
    gateway.MAX_OUTPUT = 1024
    gateway.FIXTURE = False
    gateway.recover_pending()
    class Handler(gateway.Handler):
        def do_GET(self): self.reject(403, 'test_no_retrieval')
    rejected=[]
    gateway.KIE='https://api.kie.ai'
    Handler.forward(SimpleNamespace(command='POST', path='/codex/v1/responses',
                                    reject=lambda code,label: rejected.append((code,label))), b'{}')
    assert rejected == [(403, 'external_gateway_disabled')]
    gateway.KIE='http://127.0.0.1:18767'
    server = gateway.Server(str(gateway.SOCKET), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return gateway, server

async def run():
    assert Path('/.dockerenv').exists() and CASE in {'fast', 'slow', 'timeout', 'late_terminal'}
    ROOT.mkdir(exist_ok=True)
    shutil.copytree('/observe-denial-seed', DATA)
    provenance = json.loads(Path('/observe-checkpoint-provenance.json').read_text())
    assert provenance['synthetic_only'] and provenance['status'] == 'exhausted before supervisor action'
    cfg = {'version': 1, 'mode': 'synthetic', 'online_enabled': True,
        'endpoint': 'http://127.0.0.1/codex/v1/responses',
        'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol', 'reasoning_effort': 'low',
                     'max_output_tokens': 1024, 'timeout_s': 30},
        'profiles': {}, 'allowed_actions': ['read_events', 'read_contract', 'wait', 'human_review'],
        'model_transport': 'fixture', 'external_calls_enabled': False, 'retrieval_allowed': False,
        'continuation_allowed': False, 'gateway_ack': True}
    if os.environ.get('OBSERVE_STREAM_RED') == '1':
        for key in ('model_transport', 'external_calls_enabled', 'retrieval_allowed', 'continuation_allowed', 'gateway_ack'):
            cfg.pop(key)
    save('runtime-config.json', cfg)
    os.environ.update({'DATA_DIR': str(DATA), 'SQLITE_PATH': str(DATA / 'state.db'),
        'GPT_API_KEY': 'synthetic-stream-fixture', 'GPT_BASE_URL': 'http://127.0.0.1',
        'GPT_API_MODE': 'responses', 'GPT_CHAT_PATH': '/codex/v1/responses',
        'GPT_MAX_RETRIES': '0', 'ORCHESTRATOR_RUNTIME_CONFIG': str(ROOT / 'runtime-config.json'),
        'TELEGRAM_ENABLED': 'false', 'TELEGRAM_BOT_TOKEN': '', 'FLEET_ENABLED': 'false'})
    sys.path.insert(0, str(SOURCE))
    from sqlalchemy import select
    from app.db import engine, SessionLocal
    from app.services import observation, gpt_api
    from app.services.actions_v1 import ActionExecutionControl
    from app.services.decision_state import DecisionRecord, DecisionTurn
    from app.services.actions_recovery import RecoveryAction
    from app.services.decision_v1 import get_decider
    async with engine.begin() as conn: await observation.migrate_observation_schema(conn)
    observer = observation.get_observer()
    await observer.reload()
    execution = provenance['execution_id']
    async with SessionLocal() as s:
        control = await s.get(ActionExecutionControl, execution)
        assert control.requests_used == 18 and control.enabled and not control.held
        control.request_limit = 19
        await s.commit()
    await observer.reload()
    decider = get_decider(observer)
    await decider.configure(project_id=2, execution_id=execution, enabled=True,
        initiator='offline-stream-test', rationale='Full Decision fixture through existing Unix gateway')
    upstream = ThreadingHTTPServer(('127.0.0.1', 18767), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    gateway, gateway_server = setup_gateway()
    original_save = observer.save_body
    def slow_save(*args, **kwargs):
        if CASE == 'slow': time.sleep(.025)
        return original_save(*args, **kwargs)
    def socket_client(**kwargs):
        kwargs['transport'] = httpx.AsyncHTTPTransport(uds=str(ROOT / 'llm.sock'), retries=0)
        kwargs['trust_env'] = False
        return httpx.AsyncClient(**kwargs)
    before = {str(p.relative_to(DATA)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in DATA.rglob('project.db')}
    task = None
    try:
        deadline = {'fast': 5.0, 'slow': 2.0, 'timeout': 1.5, 'late_terminal': 1.5}[CASE]
        with patch.object(gpt_api, '_async_client', socket_client), patch.object(
                gpt_api, '_sse_deadline_s', return_value=deadline), patch.object(
                gpt_api, '_headers', return_value={'Authorization': 'Bearer synthetic-fixture'}), patch.object(
                observer, 'save_body', slow_save):
            task = asyncio.create_task(observer.loop())
            until = time.monotonic() + 15
            while time.monotonic() < until:
                async with SessionLocal() as s:
                    records = (await s.scalars(select(DecisionRecord))).all()
                    if records and records[0].status in {'human_review', 'done'}: break
                await asyncio.sleep(.05)
            else: raise AssertionError('Decision did not reach terminal review')
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        until = time.monotonic() + 5
        while time.monotonic() < until:
            with sqlite3.connect(gateway.LEDGER) as db:
                row = db.execute('select state from requests order by ordinal desc limit 1').fetchone()
            if row and row[0] != 'reserved': break
            await asyncio.sleep(.05)
        gateway_server.shutdown(); gateway_server.server_close()
        upstream.shutdown(); upstream.server_close()
    async with SessionLocal() as s:
        record = (await s.scalars(select(DecisionRecord))).one()
        turns = (await s.scalars(select(DecisionTurn).order_by(DecisionTurn.id))).all()
        control = await s.get(ActionExecutionControl, execution)
        recovery = (await s.scalars(select(RecoveryAction))).all()
    after = {str(p.relative_to(DATA)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in DATA.rglob('project.db')}
    with sqlite3.connect(gateway.LEDGER) as db:
        gateway_rows = db.execute('select ordinal,state,http_status,credits,body_sha256 from requests').fetchall()
        columns = [c[1] for c in db.execute('pragma table_info(requests)')]
        upstream_complete = db.execute('select upstream_complete from requests').fetchone()[0] if 'upstream_complete' in columns else None
        ack = db.execute('select application_received_at,application_accepted_at,storage_ms,socket_write_ms from requests').fetchone() if 'application_received_at' in columns else None
        timing = db.execute('select upstream_first_at,upstream_completed_at,downstream_last_write_at,downstream_finished_at from requests').fetchone() if 'upstream_first_at' in columns else None
        batch_count = db.execute('select count(*) from event_batches').fetchone()[0] if 'event_batches' in [r[0] for r in db.execute("select name from sqlite_master where type='table'")] else 0
        terminal_path,accumulated_path,usage_json = db.execute('select terminal_body_path,accumulated_body_path,usage_json from requests').fetchone()
        batches = db.execute('select body_path,sha256 from event_batches order by batch_index').fetchall() if batch_count else []
    assert all(hashlib.sha256((gateway.BODY_ROOT / path).read_bytes()).hexdigest() == sha for path,sha in batches)
    if upstream_complete:
        terminal = json.loads((gateway.BODY_ROOT / terminal_path).read_text())
        accumulated = json.loads((gateway.BODY_ROOT / accumulated_path).read_text())
        assert terminal['type'] == 'response.completed' and json.loads(usage_json)['output_tokens'] == 40
        assert json.loads(accumulated['text'])['action'] == 'human_review'
    http_events = [t.payload for t in turns if t.role == 'http_event']
    ends = [t for t in http_events if t.get('kind') == 'decision_model_end']
    denied = [t for t in http_events if t.get('kind') == 'decision_retrieve_denied']
    sends = [t for t in http_events if t.get('kind') == 'decision_model_start']
    saved_gateway_bodies = list(gateway.BODY_ROOT.rglob('*.json'))
    assert all(b'synthetic-secret-for-redaction' not in item.read_bytes() and b'https://example.test' not in item.read_bytes()
               for item in saved_gateway_bodies)
    manifest = json.loads((SOURCE / 'source-manifest.json').read_text())
    loaded = {'gateway.py': hashlib.sha256((SOURCE / 'gateway.py').read_bytes()).hexdigest(),
        'app/services/gpt_api.py': hashlib.sha256(Path(gpt_api.__file__).read_bytes()).hexdigest(),
        'app/services/decision_v1.py': hashlib.sha256(Path(sys.modules['app.services.decision_v1'].__file__).read_bytes()).hexdigest()}
    assert all(manifest[path] == sha for path,sha in loaded.items())
    save('loaded-source-hashes.json', loaded)
    original_ledger,original_marker,original_degraded = gateway.LEDGER,gateway.AUDIT_FAILURE,gateway.AUDIT_DEGRADED
    gate_dir=ROOT/'failure-gate';gate_dir.mkdir(exist_ok=True)
    try:
        gateway.LEDGER=gate_dir/'storage.sqlite'
        gateway.AUDIT_FAILURE=gate_dir/'storage.marker'
        gateway.degrade('fixture_mandatory_storage_failure')
        try: gateway.reserve('POST','/codex/v1/responses')
        except RuntimeError as exc: assert 'degraded' in str(exc)
        else: raise AssertionError('storage failure did not block next POST')
        gateway.AUDIT_DEGRADED=False
        gateway.LEDGER=gate_dir/'restart.sqlite'
        gateway.AUDIT_FAILURE=gate_dir/'restart.marker'
        gateway.reserve('POST','/codex/v1/responses')
        gateway.recover_pending()
        try: gateway.reserve('POST','/codex/v1/responses')
        except RuntimeError as exc: assert 'uncertain_after_restart' in str(exc)
        else: raise AssertionError('uncertain reservation did not block restart POST')
    finally:
        gateway.LEDGER, gateway.AUDIT_FAILURE, gateway.AUDIT_DEGRADED = original_ledger,original_marker,original_degraded
    result = {'case': CASE, 'status': record.status, 'model_steps': record.model_steps,
        'assistant_proposals': len([t for t in turns if t.role == 'assistant']),
        'model_end_complete': bool(ends and ends[-1]['metadata'].get('complete')),
        'model_end_usage': ends[-1]['metadata'].get('usage') if ends else None,
        'retrieve_denials': len(denied), 'retrieval_attempts': len([t for t in http_events if t.get('kind') == 'decision_retrieve_start']),
        'request_reservations': control.requests_used - 18, 'model_starts': len(sends),
        'upstream_calls': Upstream.calls, 'upstream_events_emitted': Upstream.emitted,
        'upstream_terminal_emitted': Upstream.terminal_at is not None,
        'gateway_rows': gateway_rows, 'gateway_upstream_complete': upstream_complete,
        'request_hash_matches': bool(sends and gateway_rows[0][4] == sends[0]['metadata'].get('request_body_sha256')),
        'gateway_ack_received': bool(ack and ack[0]), 'gateway_ack_accepted': bool(ack and ack[1]),
        'gateway_storage_ms': ack[2] if ack else None, 'gateway_socket_write_ms': ack[3] if ack else None,
        'gateway_times': {'upstream_first':timing[0], 'upstream_completed':timing[1],
            'downstream_last_write':timing[2], 'downstream_finished':timing[3]} if timing else None,
        'gateway_event_batches': batch_count, 'project_db_unchanged': before == after,
        'gateway_terminal_saved': bool(terminal_path), 'gateway_accumulated_saved': bool(accumulated_path),
        'gateway_usage_saved': bool(usage_json), 'gateway_batch_hashes_valid': True,
        'storage_failure_blocks_new_post': True, 'restart_uncertain_blocks_new_post': True,
        'recovery_count': len(recovery), 'action_id': record.action_id}
    save('result.json', result)
    assert before == after and not recovery and record.action_id is None
    assert len(gateway_rows) == Upstream.calls == len(sends) == 1
    assert result['request_hash_matches']
    assert timing and timing[0] and timing[2]
    assert control.requests_used == 19
    if CASE in {'fast', 'slow'}:
        assert result['model_end_complete'] and result['assistant_proposals'] == 1
        assert upstream_complete and batch_count > 0
        assert timing[1] and timing[3]
        assert result['gateway_ack_received'] and result['gateway_ack_accepted']
    elif CASE == 'timeout':
        assert not result['model_end_complete'] and result['assistant_proposals'] == 0
        assert result['retrieve_denials'] >= 1 and result['retrieval_attempts'] == 0
        assert not upstream_complete
    else:
        assert not result['model_end_complete'] and result['assistant_proposals'] == 0
        assert result['retrieve_denials'] >= 1 and result['retrieval_attempts'] == 0
        assert upstream_complete and batch_count > 0 and not result['gateway_ack_received']
        assert timing[1]
    await engine.dispose()
    print(json.dumps({'case': CASE, 'accepted': True, 'model_posts': Upstream.calls}), flush=True)

if __name__ == '__main__':
    code = 0
    try: asyncio.run(run())
    except BaseException as exc:
        code = 1
        ROOT.mkdir(exist_ok=True)
        save('failure.json', {'type': type(exc).__name__, 'reason': str(exc)})
        import traceback; traceback.print_exc()
    raise SystemExit(code)
