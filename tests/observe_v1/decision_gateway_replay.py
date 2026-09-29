"""Offline two-turn replay through the online entrypoint and exact pilot Handler."""
import asyncio
import hashlib
import importlib.util
import json
import os
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from unittest.mock import patch

ROOT = Path('/results')
REPLAY = Path('/observe-replay')
SOURCE = Path('/observe-source')


def context(body):
    found = []
    def walk(value):
        if isinstance(value, dict):
            if 'context_version' in value and 'allowed_actions' in value:
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            for i, char in enumerate(value):
                if char == '{':
                    try:
                        parsed, _ = json.JSONDecoder().raw_decode(value[i:])
                    except ValueError:
                        continue
                    if isinstance(parsed, dict) and 'context_version' in parsed and 'allowed_actions' in parsed:
                        found.append(parsed)
    walk(body)
    if len(found) != 1:
        raise AssertionError('expected exactly one Decision context')
    return found[0]


def replay_events(ordinal):
    events = []
    for path in sorted((REPLAY / str(ordinal)).glob('events-*.json')):
        events.extend(json.loads(path.read_text(encoding='utf-8'))['events'])
    assert events and events[-1]['type'] == 'response.completed'
    return events


class Fixture(BaseHTTPRequestHandler):
    posts = []
    errors = []
    def log_message(self, *_args):
        pass
    def do_GET(self):
        type(self).errors.append('forbidden_get')
        self.send_error(403)
    def do_POST(self):
        try:
            assert self.path == '/codex/v1/responses'
            raw = self.rfile.read(int(self.headers['Content-Length']))
            body = json.loads(raw)
            ctx = context(body)
            ordinal = len(type(self).posts) + 1
            assert ordinal <= 2, 'third fixture POST forbidden'
            events = replay_events(ordinal)
            response = events[-1]['response']
            texts = [part.get('text') for item in response['output']
                     for part in item.get('content', []) if part.get('type') == 'output_text']
            assert len(texts) == 1
            answer = json.loads(texts[0])
            assert answer['action'] == ('read_contract' if ordinal == 1 else 'human_review')
            assert answer['context_version'] == ctx['context_version']
            assert set(answer['evidence_ids']).issubset(set(ctx['evidence_ids']))
            if ordinal == 2:
                encoded = json.dumps(body)
                assert all(part in encoded for part in ('read_contract', 'target_frame_count',
                                                       'semantic_quality_verified')), 'tool output absent from second request'
            type(self).posts.append({'request_sha256': hashlib.sha256(raw).hexdigest(),
                                     'response_id': response['id'], 'action': answer['action'],
                                     'bytes': len(raw), 'context_version': ctx['context_version'],
                                     'credits': events[-1].get('credits_consumed', response.get('credits_consumed'))})
            data = ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except BaseException as exc:
            type(self).errors.append(type(exc).__name__ + ':' + str(exc))
            (ROOT / 'fixture-errors.json').write_text(json.dumps(type(self).errors), encoding='utf-8')
            self.send_error(500)


async def main():
    assert Path('/.dockerenv').exists()
    ROOT.mkdir(exist_ok=True)
    fixture = ThreadingHTTPServer(('127.0.0.1', 18767), Fixture)
    fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fixture_thread.start()
    import shutil
    gateway_source = Path('/gateway-source')
    gateway_source.mkdir(exist_ok=True)
    shutil.copy2(SOURCE / 'gateway.py', gateway_source / 'gateway.py')
    shutil.copy2(SOURCE / 'scripts/diagnostics/decision_tool_cycle_gateway.py', gateway_source / 'decision_tool_cycle_gateway.py')
    shutil.copy2(SOURCE / 'config/decision-tool-cycle.example.json', gateway_source / 'pilot-config.json')
    spec = importlib.util.spec_from_file_location('pilot_gateway', gateway_source / 'decision_tool_cycle_gateway.py')
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    gateway = pilot.base
    gateway.KIE = 'http://127.0.0.1:18767'
    gateway.SOCKET = Path('/gateway/llm.sock')
    gateway.SOCKET.parent.mkdir(exist_ok=True)
    gateway.LEDGER = ROOT / 'decision-gateway.sqlite'
    gateway.BODY_ROOT = ROOT / 'gateway-bodies'
    gateway.AUDIT_FAILURE = ROOT / 'gateway-audit-failure.marker'
    gateway.DRAIN_REQUEST = ROOT / 'gateway-drain-requested.marker'
    gateway.recover_pending()
    if os.environ.get('OBSERVE_REPLAY_STORAGE_FAILURE') == '1':
        original_save = gateway.save_batch
        original_connect = gateway.connect
        def failing_save(*args, **kwargs):
            def readonly_connect():
                db = original_connect()
                db.execute('PRAGMA query_only=ON')
                return db
            with patch.object(gateway, 'connect', readonly_connect):
                return original_save(*args, **kwargs)
        gateway.save_batch = failing_save
    server = gateway.Server(str(gateway.SOCKET), pilot.Handler)
    gateway_thread = threading.Thread(target=server.serve_forever, daemon=True)
    gateway_thread.start()
    try:
        spec = importlib.util.spec_from_file_location('decision_tool_cycle_entry', SOURCE / 'scripts/diagnostics/decision_tool_cycle_entry.py')
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        await entry.run('synthetic-readonly-test-key')
        result = json.loads((ROOT / 'result.json').read_text(encoding='utf-8'))
        if os.environ.get('OBSERVE_REPLAY_STORAGE_FAILURE') == '1':
            assert result['status'] == 'human_review' and result['model_steps'] == 1
            assert not result['proposals']
            assert not any(t.get('action') == 'read_contract' for t in result['tool_results'])
            assert len(Fixture.posts) == 1 and result['business_unchanged']
            assert gateway.AUDIT_FAILURE.exists()
            markers = [json.loads(line) for line in gateway.AUDIT_FAILURE.read_text().splitlines()]
            assert any(m['operation'] == 'save_batch_transaction' and
                       m['exception_type'] == 'OperationalError' and
                       m['sqlite_errorname'] for m in markers)
            try:
                gateway.reserve('POST', '/codex/v1/responses')
            except RuntimeError as exc:
                assert 'degraded' in str(exc)
            else:
                raise AssertionError('new send not blocked')
            (ROOT / 'replay-summary.json').write_text(json.dumps({
                'storage_failure': True, 'posts': len(Fixture.posts), 'tools': 0,
                'successful_acks': 0, 'blocked_new_send': True,
                'sqlite_errorname': next(m['sqlite_errorname'] for m in markers if m['sqlite_errorname']),
                'business_unchanged': True, 'real_api_calls': 0}, indent=2), encoding='utf-8')
            print(json.dumps({'posts': 1, 'storage_failure': True, 'blocked_new_send': True}))
            return
        assert result['status'] == 'human_review'
        assert result['model_steps'] == 2 and result['business_unchanged']
        assert [p['action'] for p in result['proposals']] == ['read_contract', 'human_review']
        assert any(t.get('action') == 'read_contract' for t in result['tool_results'])
        assert len(Fixture.posts) == 2 and not Fixture.errors
        with sqlite3.connect(gateway.LEDGER) as db:
            rows = db.execute('SELECT ordinal,state,response_id,credits,usage_json,terminal_body_path,accumulated_body_path,application_received_at,application_accepted_at,event_count FROM requests ORDER BY ordinal').fetchall()
            acks = db.execute('SELECT response_id,stage,attempts,accepted FROM ack_events ORDER BY response_id,stage').fetchall()
            pending = db.execute("SELECT COUNT(*) FROM requests WHERE state='reserved'").fetchone()[0]
            batches = db.execute('SELECT request_ordinal,body_path,sha256 FROM event_batches').fetchall()
        assert len(rows) == 2 and pending == 0 and len(acks) == 4
        assert not gateway.AUDIT_FAILURE.exists()
        assert all(r[1] == 'complete' and r[4] and r[5] and r[6] and r[7] and r[8] and r[9] > 0 for r in rows)
        assert all(a[2] == 1 and a[3] == 1 for a in acks)
        assert sum(r[3] for r in rows) == sum(p['credits'] for p in Fixture.posts)
        for ordinal, (row, post) in enumerate(zip(rows, Fixture.posts), start=1):
            archived_terminal = replay_events(ordinal)[-1]
            saved_terminal = json.loads((gateway.BODY_ROOT / row[5]).read_text(encoding='utf-8'))
            assert row[2] == post['response_id'] == saved_terminal['response']['id']
            assert row[3] == post['credits'] == saved_terminal['credits_consumed']
            assert json.loads(row[4]) == archived_terminal['response']['usage']
        references = set()
        for ordinal, ref, sha in batches:
            assert hashlib.sha256((gateway.BODY_ROOT / ref).read_bytes()).hexdigest() == sha
            references.add(ref)
        for row in rows:
            for ref in row[5:7]:
                path = gateway.BODY_ROOT / ref
                assert path.is_file() and path.stat().st_size > 0
                assert hashlib.sha256(path.read_bytes()).hexdigest() == path.stem.rsplit('-', 1)[1]
                references.add(ref)
        actual = {str(path.relative_to(gateway.BODY_ROOT)) for path in gateway.BODY_ROOT.rglob('*.json')}
        assert actual == references, 'unindexed or missing body files'
        gateway.DRAIN_REQUEST.touch()
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(gateway.SOCKET)), base_url='http://localhost', timeout=10) as client:
            assert client.post('/local/gateway-stop').status_code == 200
        gateway_thread.join(timeout=10)
        assert not gateway_thread.is_alive()
        summary = {'source_entry': hashlib.sha256((SOURCE / 'scripts/diagnostics/decision_tool_cycle_entry.py').read_bytes()).hexdigest(),
                   'pilot_handler': hashlib.sha256((gateway_source / 'decision_tool_cycle_gateway.py').read_bytes()).hexdigest(),
                   'base_gateway': hashlib.sha256((gateway_source / 'gateway.py').read_bytes()).hexdigest(),
                   'posts': Fixture.posts, 'acks': len(acks), 'pending': pending,
                   'batch_count': len(batches), 'business_unchanged': True,
                   'tool_result_present': True, 'graceful_gateway_stop': True,
                   'provider_gets': 0, 'real_api_calls': 0}
        (ROOT / 'replay-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps({'posts': 2, 'acks': 4, 'pending': 0, 'graceful_stop': True}))
    finally:
        if gateway_thread.is_alive():
            server.shutdown()
        server.server_close()
        fixture.shutdown()
        fixture.server_close()


if __name__ == '__main__':
    asyncio.run(main())
