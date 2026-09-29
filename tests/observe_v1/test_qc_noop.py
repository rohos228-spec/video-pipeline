"""Original batch/operator/model parser; only external HTTP answers are fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from app.services.apply_ops_batches import run_apply_ops_batched
from app.services import gpt_api
from app.settings import settings


class QcNoopHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.posts = []
        self.applied = []
        self.answer = '{"ops":[]}'
        self.fail = False
        self.complete = True
        self.context = {'frames': [{'uuid': 'frame-one', 'number': 1,
            'voiceover_text': 'Синтетический диктор открывает дверь и выходит на улицу.',
            'fields': {'закадр': 'Синтетический диктор открывает дверь и выходит на улицу.'}}]}
        self.path = self.root / 'db_frames.json'
        self.path.write_text(json.dumps(self.context, ensure_ascii=False))
        for name, value in [('gpt_max_retries', 0), ('gpt_api_mode', 'responses'),
                            ('gpt_chat_path', '/codex/v1/responses')]:
            p = patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)
        def response(request):
            self.posts.append(request)
            if self.fail:
                return httpx.Response(500, request=request, content=b'synthetic provider failure')
            events = [{'type': 'response.created', 'response': {'id': 'resp_synthetic'}},
                      {'type': 'response.output_text.delta', 'delta': self.answer}]
            if self.complete:
                events.append({'type': 'response.completed', 'response': {
                    'id': 'resp_synthetic', 'status': 'completed',
                    'output': [{'content': [{'type': 'output_text', 'text': self.answer}]}],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}}})
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content=''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode())
        def client(**kw):
            return httpx.AsyncClient(transport=httpx.MockTransport(response), **kw)
        p = patch.object(gpt_api, '_async_client', client)
        p.start()
        self.addCleanup(p.stop)

    async def batch(self, allowed=True):
        async def apply(payload): self.applied.append(payload)
        return await run_apply_ops_batched(project_dir=self.root, node_key='arbitrary-id',
            role='assist', output_mode='project_file', prompt='Built-in no-op contract',
            accompanying='', db_ctx=self.context, ctx_path=self.path, project_id=2,
            dense=False, footer_kind='shots_qc', force_full=True, allow_empty_ops=allowed,
            apply_fn=apply)

    async def test_completed_authorized_noop_uses_one_post_and_no_business_apply(self):
        result = await self.batch()
        self.assertEqual(len(self.posts), 1, 'valid QC no-op must not cause JSON retry')
        self.assertEqual(result.apply_ops, {'ops': []})
        self.assertEqual(self.applied, [])

    async def test_provider_error_is_not_successful_noop(self):
        self.fail = True
        with self.assertRaises(Exception): await self.batch()
        self.assertEqual(self.applied, [])

    async def test_malformed_json_is_not_successful_noop(self):
        self.answer = 'not valid JSON'
        with self.assertRaises(Exception): await self.batch()
        self.assertEqual(self.applied, [])

    async def test_explicit_refusal_is_not_successful_noop(self):
        self.answer = '{"ops":[],"error":"synthetic refusal"}'
        with self.assertRaises(Exception): await self.batch()
        self.assertEqual(self.applied, [])

    async def test_creating_node_still_rejects_empty_payload(self):
        with self.assertRaises(RuntimeError): await self.batch(allowed=False)
        self.assertEqual(len(self.posts), 2, 'original JSON retry remains for mandatory writes')

    async def test_real_correction_reaches_original_batch_apply_callback_once(self):
        self.answer = '{"ops":[{"frame_uuid":"frame-one","fields":{"место":"улица"}}]}'
        await self.batch()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.applied), 1)


if __name__ == '__main__': unittest.main()
