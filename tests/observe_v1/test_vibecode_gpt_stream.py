import json
import unittest
from unittest.mock import patch

import httpx

from app.services import gpt_api, observation
from app.services.llm_completion import response_complete


def sse(*chunks):
    return [f"data: {json.dumps(c)}" if isinstance(c, dict) else c for c in chunks]


def delta(text, finish=None):
    return {"id": "chatcmpl-1", "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}]}


USAGE_CHUNK = {"id": "chatcmpl-1", "choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


class _Stream:
    status_code = 200

    def __init__(self, lines, error=None):
        self.lines, self.error = lines, error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def aiter_lines(self):
        for line in self.lines:
            yield line
        if self.error is not None:
            raise self.error


class _Client:
    def __init__(self, lines, error=None):
        self.lines, self.error = lines, error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def stream(self, *_a, **_k):
        return _Stream(self.lines, self.error)


class ChatStreamFinish(unittest.IsolatedAsyncioTestCase):
    async def stream(self, lines, error=None):
        with patch.object(gpt_api, "_async_client", lambda **_k: _Client(lines, error)), \
                patch.object(observation, "_track_api_cost"):
            return await gpt_api._chat_completions_stream(
                url="http://x", headers={}, body={}, timeout=10, use_model="gpt-5.6-sol")

    async def test_normal_answer_exposes_finish_reason_and_is_complete(self):
        result = await self.stream(sse(delta("he"), delta("llo", "stop"), USAGE_CHUNK, "data: [DONE]"))
        self.assertEqual(result.text, "hello")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.raw["finish_reason"], "stop")
        self.assertEqual(result.usage, {"prompt_tokens": 7, "completion_tokens": 3})
        self.assertTrue(response_complete(result.raw, result.finish_reason))

    async def test_stream_that_never_sent_finish_reason_is_not_reported_as_stop(self):
        result = await self.stream(sse(delta("hello"), "data: [DONE]"))
        self.assertEqual(result.text, "hello")
        self.assertEqual(result.finish_reason, "")
        self.assertNotIn("finish_reason", result.raw)
        self.assertFalse(response_complete(result.raw, result.finish_reason))

    async def test_interrupted_stream_is_salvaged_but_not_complete(self):
        result = await self.stream(sse(delta("part", "stop")), error=httpx.ReadError("cut"))
        self.assertTrue(result.raw["sse_salvaged"])
        self.assertFalse(response_complete(result.raw, result.finish_reason))

    async def test_length_finish_is_visible_as_truncated(self):
        from app.services.llm_completion import response_truncated
        result = await self.stream(sse(delta("cut off", "length")))
        self.assertEqual(result.finish_reason, "length")
        self.assertTrue(response_truncated(result.raw, result.finish_reason))
        self.assertFalse(response_complete(result.raw, result.finish_reason))

    async def test_json_body_without_sse_uses_message_finish_reason(self):
        blob = json.dumps({"id": "c", "choices": [{"message": {"content": "whole"}, "finish_reason": "stop"}]})
        result = await self.stream([blob])
        self.assertEqual(result.text, "whole")
        self.assertEqual(result.finish_reason, "stop")
        self.assertTrue(response_complete(result.raw, result.finish_reason))

    async def test_parse_helper_keeps_its_default_for_other_callers(self):
        text, finish, _ = gpt_api.parse_chat_completions_sse_lines(sse(delta("x")))
        self.assertEqual((text, finish), ("x", "stop"))
