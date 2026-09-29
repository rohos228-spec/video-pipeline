import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

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

if __name__ == '__main__':
    unittest.main()
