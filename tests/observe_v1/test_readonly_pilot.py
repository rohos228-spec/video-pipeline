import importlib.util
import json
import tempfile
import concurrent.futures
import hashlib
import socket
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

spec = importlib.util.spec_from_file_location('readonly_qc_pilot', Path(__file__).resolve().parents[2] / 'scripts/readonly_qc_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)

CONTEXT = {'context_version': 'frozen-example', 'evidence_ids': ['e1'],
           'allowed_actions': ['wait', 'human_review'], 'events': [{'id': 'e1', 'parsed': True}],
           'contract': {'requires_nonempty_ops': True}, 'state': {'native_retry': 'in_flight'}}

def answer(action='wait'):
    return {'assessment': {'json_syntax': 'valid', 'required_payload': 'absent',
            'native_retry': 'in_flight', 'possible_cause': 'Not established by this slice.',
            'uncertainties': ['No retry response yet.'], 'evidence_ids': ['e1']},
            'recommendation': {'action': action, 'arguments': {}, 'evidence_ids': ['e1'],
            'context_version': CONTEXT['context_version'],
            'explanation': 'Wait for the active native request; syntax alone is not successful output.'}}

def fixture(value=None, *, complete=True, fail=False):
    seen = []
    def handle(request):
        seen.append(request)
        if fail:
            raise httpx.ReadTimeout('synthetic timeout', request=request)
        text = json.dumps(value if value is not None else answer())
        events = [{'type': 'response.created', 'response': {'id': 'resp_fixture'}},
                  {'type': 'response.output_text.delta', 'delta': text}]
        if complete:
            events.append({'type': 'response.completed', 'response': {
                'id': 'resp_fixture', 'status': 'completed',
                'output': [{'content': [{'type': 'output_text', 'text': text}]}],
                'usage': {'input_tokens': 50, 'output_tokens': 90}}})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode())
    return httpx.MockTransport(handle), seen

class ReadOnlyPilot(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    async def test_existing_adapter_returns_structured_recommendation_without_dispatch(self):
        transport, seen = fixture()
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=transport)
        self.assertEqual(result['status'], 'evaluated')
        self.assertEqual(result['recommendation']['action'], 'wait')
        self.assertFalse(result['executed'])
        self.assertEqual(len(seen), 1)
        body = json.loads(seen[0].content)
        self.assertEqual(body['max_output_tokens'], 1024)
        self.assertNotIn('tools', body)

    async def test_restart_cannot_send_second_post(self):
        transport, seen = fixture()
        await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=transport)
        other, later = fixture()
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=other)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(len(seen), 1)
        self.assertEqual(later, [])

    async def test_uncertain_result_cannot_send_again(self):
        transport, seen = fixture(fail=True)
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=transport)
        self.assertEqual(result['status'], 'uncertain')
        other, later = fixture()
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=other)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(len(seen), 1)
        self.assertEqual(later, [])

    async def test_partial_response_has_no_get_or_continuation(self):
        transport, seen = fixture(complete=False)
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=transport)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual([r.method for r in seen], ['POST'])

    async def test_mutating_recommendation_is_rejected_without_execution(self):
        transport, seen = fixture(answer('retry_same'))
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=transport)
        self.assertEqual(result['status'], 'invalid')
        self.assertFalse(result['executed'])
        self.assertEqual(len(seen), 1)

    async def test_read_context_proposal_does_not_start_another_turn(self):
        value = answer('read_contract')
        transport, seen = fixture(value)
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=transport)
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(len(seen), 1)

    async def test_online_disabled_without_synthetic_transport(self):
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['reason'], 'online_not_authorized')

    async def test_real_archived_context_uses_same_adapter_and_exact_prepared_body(self):
        context = json.loads(Path('/observe-source/pilot-context.json').read_text())
        self.assertEqual(context['snapshot']['cutoff_event_seq'], 4140)
        self.assertTrue(all(int(e[1:]) <= 4140 for e in context['evidence_ids']))
        value = answer()
        value['assessment']['evidence_ids'] = ['e4131']
        value['assessment']['native_retry'] = 'may_be_active'
        value['recommendation']['evidence_ids'] = ['e4131', 'e4138']
        value['recommendation']['context_version'] = context['context_version']
        transport, seen = fixture(value)
        root = Path('/results/archived-context-synthetic-check')
        result = await pilot.evaluate_once(context, root, 'synthetic-archive-check', synthetic_transport=transport)
        self.assertEqual(result['status'], 'evaluated')
        prepared = pilot.request_body(context)
        self.assertEqual(json.loads(seen[0].content), prepared)
        self.assertEqual(seen[0].content, pilot.encoded(prepared))
        Path('/results/prepared-request.json').write_bytes(pilot.encoded(prepared))
        Path('/results/prepared-request-metadata.json').write_text(json.dumps({
            'bytes': len(pilot.encoded(prepared)), 'model': prepared['model'],
            'max_output_tokens': prepared['max_output_tokens'],
            'synthetic_check_only': True, 'actual_provider_calls': 0}))

    async def test_atomic_parallel_reservations_allow_one(self):
        def claim(_):
            ledger = pilot.Ledger(self.root, 'parallel', CONTEXT)
            try:
                ledger.reserve()
                return True
            except pilot.GuardStop:
                return False
            finally:
                ledger.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim, range(4))), 1)

    async def test_reserved_after_crash_is_not_reissued(self):
        ledger = pilot.Ledger(self.root, 'crash', CONTEXT)
        ledger.reserve()
        ledger.db.close()
        transport, seen = fixture()
        result = await pilot.evaluate_once(CONTEXT, self.root, 'crash', synthetic_transport=transport)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(seen, [])

    async def test_changed_context_cannot_reuse_execution(self):
        ledger = pilot.Ledger(self.root, 'pilot-test', CONTEXT)
        ledger.db.close()
        transport, seen = fixture()
        result = await pilot.evaluate_once({**CONTEXT, 'context_version': 'changed'}, self.root, 'pilot-test', synthetic_transport=transport)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(seen, [])

    async def test_mandatory_audit_failure_prevents_wire(self):
        transport, seen = fixture()
        with patch.object(pilot.Ledger, 'event', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                await pilot.evaluate_once(CONTEXT, self.root, 'audit-test', synthetic_transport=transport)
        self.assertEqual(seen, [])

    async def test_unknown_evidence_stale_version_and_bad_schema_are_not_repaired(self):
        for mutate in ('evidence', 'version', 'schema'):
            value = answer()
            if mutate == 'evidence': value['assessment']['evidence_ids'] = ['foreign-event']
            if mutate == 'version': value['recommendation']['context_version'] = 'stale'
            if mutate == 'schema': value['extra'] = 'unexpected'
            transport, seen = fixture(value)
            result = await pilot.evaluate_once(CONTEXT, self.root / mutate, 'pilot-test', synthetic_transport=transport)
            self.assertEqual(result['status'], 'invalid')
            self.assertEqual(len(seen), 1)

    async def test_complete_but_truncated_json_cannot_trigger_second_post(self):
        value = answer()
        transport, seen = fixture(value)
        from app.services import gpt_api
        original = gpt_api.parse_responses_sse_lines
        def truncated(lines):
            text, finish, raw, response_id = original(lines)
            return text[:-4], finish, raw, response_id
        with patch.object(gpt_api, 'parse_responses_sse_lines', side_effect=truncated):
            result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=transport)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual([r.method for r in seen], ['POST'])

    async def test_wrong_route_get_and_oversized_request_are_blocked_before_wire(self):
        transport, seen = fixture()
        ledger = pilot.Ledger(self.root, 'guard-test', CONTEXT)
        guard = pilot.GuardTransport(ledger, transport)
        try:
            for request in [httpx.Request('GET', pilot.ENDPOINT),
                            httpx.Request('POST', 'https://other.invalid/codex/v1/responses'),
                            httpx.Request('POST', pilot.ENDPOINT, json={'model': pilot.MODEL, 'max_output_tokens': 1024, 'input': 'x' * 30001})]:
                with self.assertRaises(pilot.GuardStop): await guard.handle_async_request(request)
            self.assertEqual(seen, [])
        finally:
            ledger.db.close()

    async def test_real_path_requires_gateway_and_container(self):
        result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', online_enabled=True)
        self.assertEqual(result['reason'], 'gateway_and_authorized_bearer_required')
        with patch.object(pilot.Path, 'exists', return_value=False):
            result = await pilot.evaluate_once(CONTEXT, self.root, 'pilot-test', synthetic_transport=fixture()[0])
        self.assertEqual(result['reason'], 'isolated_container_required')

    async def test_gateway_transport_uses_only_unix_socket_route(self):
        transport, seen = fixture()
        with patch.object(httpx, 'AsyncHTTPTransport', return_value=transport):
            result = await pilot.evaluate_once(CONTEXT, self.root, 'gateway-test',
                online_enabled=True, gateway_socket='/gateway/fixture.sock', bearer='synthetic-pilot-key')
        self.assertEqual(result['status'], 'evaluated')
        self.assertEqual(len(seen), 1)
        self.assertEqual(str(seen[0].url), 'http://gateway/codex/v1/responses')

    async def test_assessment_validation_rejects_invalid_types_and_claims(self):
        for key, bad in [('json_syntax', 'success'), ('required_payload', True),
                         ('native_retry', 'completed'), ('possible_cause', ''),
                         ('uncertainties', 'none'), ('evidence_ids', [])]:
            value = answer()
            value['assessment'][key] = bad
            with self.assertRaises(ValueError): pilot.validate_assessment(json.dumps(value), CONTEXT)
        for value in [[], {'assessment': {}}]:
            with self.assertRaises(ValueError): pilot.validate_assessment(json.dumps(value), CONTEXT)
        value = answer()
        value['assessment'] = []
        with self.assertRaises(ValueError): pilot.validate_assessment(json.dumps(value), CONTEXT)

    async def test_existing_gateway_enforces_body_one_post_no_get_and_restart(self):
        binding_spec = importlib.util.spec_from_file_location('pilot_gateway', '/observe-source/pilot_gateway.py')
        binding = importlib.util.module_from_spec(binding_spec)
        binding_spec.loader.exec_module(binding)
        data = pilot.encoded(pilot.request_body(CONTEXT))
        config = {'endpoint': pilot.ENDPOINT, 'model': pilot.MODEL, 'request_limit': 1,
                  'max_output_tokens': 1024, 'max_request_bytes': 30000,
                  'execute_proposals': False, 'retrieval_get': False, 'tools': [],
                  'prepared_request_sha256': hashlib.sha256(data).hexdigest()}
        base, handler = binding.bind('/observe-source/existing_gateway.py', config,
                                     self.root, self.root / 'llm.sock')
        upstream = []
        real_client = httpx.Client
        text = json.dumps(answer())
        completion = {'type': 'response.completed', 'response': {'id': 'resp_fixture',
                      'status': 'completed', 'usage': {'input_tokens': 1, 'output_tokens': 1},
                      'credits_consumed': 0, 'output': [{'content': [{'type': 'output_text', 'text': text}]}]}}
        def synthetic_provider(request):
            upstream.append(request)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  stream=httpx.ByteStream(('data: ' + json.dumps(completion) + '\n\n').encode()))
        def upstream_client(**kwargs):
            return real_client(transport=httpx.MockTransport(synthetic_provider), **kwargs)
        server = base.Server(str(base.SOCKET), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(base.httpx, 'Client', side_effect=upstream_client):
                with real_client(transport=httpx.HTTPTransport(uds=str(base.SOCKET)), timeout=3) as client:
                    headers = {'Authorization': 'Bearer synthetic-pilot-key'}
                    self.assertEqual(client.get('http://gateway/codex/v1/responses/resp_fixture').status_code, 403)
                    self.assertEqual(client.post('http://gateway/codex/v1/responses', headers=headers, content=b'{}').status_code, 403)
                    self.assertEqual(client.post('http://gateway/codex/v1/responses', headers=headers, content=data).status_code, 200)
                    base.recover_pending()
                    self.assertEqual(client.post('http://gateway/codex/v1/responses', headers=headers, content=data).status_code, 429)
            self.assertEqual(len(upstream), 1)
            self.assertEqual(str(upstream[0].url), pilot.ENDPOINT)
            self.assertEqual(upstream[0].content, data)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    async def test_container_and_child_cannot_reach_foreign_address(self):
        import subprocess, sys
        s = socket.socket()
        try:
            self.assertNotEqual(s.connect_ex(('192.0.2.1', 9)), 0)
        finally:
            s.close()
        child = subprocess.run([sys.executable, '-c',
            "import socket; s=socket.socket(); s.settimeout(2); print(s.connect_ex(('192.0.2.1',9))); s.close()"],
            capture_output=True, text=True, timeout=5, check=True)
        self.assertNotEqual(int(child.stdout.strip()), 0)

    async def test_gateway_online_entry_is_disabled_and_policy_cannot_expand(self):
        import runpy, sys
        cfg = {'online_enabled': False}
        path = self.root / 'disabled.json'
        path.write_text(json.dumps(cfg))
        with patch.object(sys, 'argv', ['pilot_gateway.py', str(path)]):
            with self.assertRaisesRegex(SystemExit, 'online disabled'):
                runpy.run_path('/observe-source/pilot_gateway.py', run_name='__main__')
        binding_spec = importlib.util.spec_from_file_location('pilot_gateway', '/observe-source/pilot_gateway.py')
        binding = importlib.util.module_from_spec(binding_spec)
        binding_spec.loader.exec_module(binding)
        with self.assertRaises(ValueError):
            binding.bind('/observe-source/existing_gateway.py', {'endpoint': 'https://foreign.invalid'},
                         self.root, self.root / 'llm.sock')

    async def test_gateway_boot_uses_fresh_ledger_and_refuses_existing_socket(self):
        import runpy, sys, socketserver
        path = self.root / 'local-boot.json'
        cfg = {'online_enabled': True, 'endpoint': pilot.ENDPOINT, 'model': pilot.MODEL,
               'request_limit': 1, 'max_output_tokens': 1024, 'max_request_bytes': 30000,
               'execute_proposals': False, 'retrieval_get': False, 'tools': [],
               'prepared_request_sha256': hashlib.sha256(pilot.encoded(pilot.request_body(CONTEXT))).hexdigest()}
        path.write_text(json.dumps(cfg))
        socket_path = Path('/gateway/llm.sock')
        self.assertFalse(socket_path.exists())
        argv = ['pilot_gateway.py', str(path), '/observe-source/existing_gateway.py', str(self.root)]
        try:
            with patch.object(sys, 'argv', argv), patch.object(socketserver.BaseServer, 'serve_forever') as serve:
                runpy.run_path('/observe-source/pilot_gateway.py', run_name='__main__')
                serve.assert_called_once()
                self.assertTrue((self.root / 'pilot-gateway.sqlite').exists())
                with self.assertRaisesRegex(SystemExit, 'Existing socket'):
                    runpy.run_path('/observe-source/pilot_gateway.py', run_name='__main__')
        finally:
            if socket_path.exists(): socket_path.unlink()
        value = answer()
        value['recommendation']['evidence_ids'] = []
        with self.assertRaises(ValueError): pilot.validate_assessment(json.dumps(value), CONTEXT)

if __name__ == '__main__':
    unittest.main()
