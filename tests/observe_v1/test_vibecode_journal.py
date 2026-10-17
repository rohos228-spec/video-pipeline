import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import observation
from app.services.gpt_api import GptApiError
from app.services.llm_override import NodeLlmOverride, use_override


def vibecode_override():
    return NodeLlmOverride("gpt-5.6-sol", "stable", "text", "vibecode", "gpt-5.6-sol")


def kie_override():
    return NodeLlmOverride("gpt-5-6-sol", "stable", "text", "kie", "gpt-5-6-sol", "low")


def chat_result(*, finish="stop", raw=None):
    return SimpleNamespace(text="ok", model="gpt-5.6-sol", response_id="chatcmpl-1", finish_reason=finish,
        usage={"prompt_tokens": 3, "completion_tokens": 2},
        raw=raw if raw is not None else ({"id": "chatcmpl-1", "finish_reason": finish} if finish else {"id": "chatcmpl-1"}))


class JournalProviderFacts(unittest.IsolatedAsyncioTestCase):
    def ctx(self, mode="observe"):
        return SimpleNamespace(scope=SimpleNamespace(mode=mode, project_id=1, execution_id="e1", gaps=[]),
            last_request_id=None, purpose="node",
            observer=SimpleNamespace(failures={}, commit_uncertainty={}, master=None))

    async def run_request(self, override, outcome, *, mode="observe", url="https://vibecode.moe/v1/chat/completions"):
        async def transport(**_kwargs):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        hold = AsyncMock()
        token = observation._attempt.set(self.ctx(mode))
        try:
            with patch.object(observation, "record_fact", new_callable=AsyncMock) as fact, \
                    patch.object(observation, "_track_api_cost") as cost, \
                    patch.object(observation, "hold_uncertain_request", hold), \
                    patch("app.services.actions_recovery.check_body", new_callable=AsyncMock), \
                    patch("app.services.actions_v1.ActionSupervisor") as supervisor:
                supervisor.return_value.charge_request = AsyncMock()
                raised = None
                with use_override(override):
                    try:
                        await observation.observe_model(transport)(url=url, body={"model": "m", "messages": []}, use_model="gpt-5.6-sol")
                    except BaseException as exc:
                        raised = exc
            facts = {c.args[0]: c.args[1] for c in fact.call_args_list}
            return facts, raised, hold, cost
        finally:
            observation._attempt.reset(token)

    async def test_vibecode_request_records_provider_and_model_in_start_and_end(self):
        facts, raised, _, cost = await self.run_request(vibecode_override(), chat_result())
        self.assertIsNone(raised)
        self.assertEqual(facts["model_start"]["provider"], "vibecode")
        self.assertEqual(facts["model_start"]["model"], "gpt-5.6-sol")
        self.assertEqual(facts["model_end"]["provider"], "vibecode")
        self.assertEqual(facts["model_end"]["model"], "gpt-5.6-sol")
        self.assertEqual(cost.call_args.kwargs["provider"], "vibecode")

    async def test_kie_request_records_kie_provider(self):
        result = SimpleNamespace(text="ok", model="gpt-5-6-sol", response_id="r", finish_reason="completed",
            usage={}, raw={"status": "completed"})
        facts, _, _, cost = await self.run_request(kie_override(), result, url="https://api.kie.ai/codex/v1/responses")
        self.assertEqual(facts["model_start"]["provider"], "kie")
        self.assertEqual(facts["model_end"]["provider"], "kie")
        self.assertTrue(facts["model_end"]["complete"])
        self.assertEqual(cost.call_args.kwargs["provider"], "kie")

    async def test_normal_chat_completions_answer_is_complete(self):
        facts, _, _, _ = await self.run_request(vibecode_override(), chat_result(finish="stop"))
        self.assertIs(facts["model_end"]["complete"], True)

    async def test_stream_without_finish_reason_is_not_complete(self):
        facts, _, _, _ = await self.run_request(vibecode_override(), chat_result(finish=""))
        self.assertIs(facts["model_end"]["complete"], False)

    async def test_salvaged_stream_is_not_complete(self):
        raw = {"id": "c", "finish_reason": "stop", "sse_salvaged": True}
        facts, _, _, _ = await self.run_request(vibecode_override(), chat_result(raw=raw))
        self.assertIs(facts["model_end"]["complete"], False)

    async def test_nonstream_choice_finish_reason_counts(self):
        raw = {"id": "c", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        facts, _, _, _ = await self.run_request(vibecode_override(), chat_result(finish="", raw=raw))
        self.assertIs(facts["model_end"]["complete"], True)

    async def test_model_error_records_provider_and_model(self):
        exc = GptApiError("GPT HTTP 402: x", context={"status_code": 402, "retryable": False, "llm_provider": "vibecode"})
        facts, raised, _, _ = await self.run_request(vibecode_override(), exc)
        self.assertIs(raised, exc)
        self.assertEqual(facts["model_error"]["provider"], "vibecode")
        self.assertEqual(facts["model_error"]["model"], "gpt-5.6-sol")
        self.assertEqual(facts["model_error"]["error_code"], "gpt_credits")
        self.assertEqual(facts["model_error"]["status_code"], 402)
        self.assertIs(facts["model_error"]["uncertain"], False)

    async def test_timeout_after_stream_started_is_uncertain(self):
        exc = GptApiError("GPT(chat/stream) empty after ReadTimeout", context={
            "retryable": True, "error_kind": "empty_stream", "stream_error": "ReadTimeout"})
        facts, _, _, _ = await self.run_request(vibecode_override(), exc)
        self.assertIs(facts["model_error"]["uncertain"], True)
        self.assertEqual(facts["model_error"]["error_code"], "gpt_timeout")

    async def test_http_5xx_and_429_are_certain_rejections_with_catalog_codes(self):
        for status, code in ((429, "gpt_rate_limit"), (503, "gpt_server")):
            with self.subTest(status=status):
                exc = GptApiError(f"GPT HTTP {status}", context={"status_code": status, "retryable": True})
                facts, _, _, _ = await self.run_request(vibecode_override(), exc)
                self.assertEqual(facts["model_error"]["error_code"], code)
                self.assertIs(facts["model_error"]["uncertain"], False)

    async def test_managed_truncated_chat_answer_holds_without_repeat_post(self):
        _, raised, hold, _ = await self.run_request(vibecode_override(), chat_result(finish="length"), mode="managed")
        self.assertIsInstance(raised, RuntimeError)
        hold.assert_awaited_once()

    async def test_managed_normal_chat_answer_is_not_held(self):
        _, raised, hold, _ = await self.run_request(vibecode_override(), chat_result(finish="stop"), mode="managed")
        self.assertIsNone(raised)
        hold.assert_not_awaited()

    async def test_managed_read_timeout_holds(self):
        import httpx
        _, raised, hold, _ = await self.run_request(vibecode_override(), httpx.ReadTimeout("t"), mode="managed")
        self.assertIsInstance(raised, httpx.ReadTimeout)
        hold.assert_awaited_once()


class EffectiveProvider(unittest.TestCase):
    def test_override_wins_over_header_setting(self):
        from app.settings import settings
        with patch.object(type(settings), "text_llm_is_vibecode", new_callable=lambda: property(lambda self: False)):
            with use_override(vibecode_override()):
                self.assertEqual(observation.effective_text_provider(), "vibecode")
            self.assertEqual(observation.effective_text_provider(), "kie")
        with patch.object(type(settings), "text_llm_is_vibecode", new_callable=lambda: property(lambda self: True)):
            self.assertEqual(observation.effective_text_provider(), "vibecode")
            with use_override(kie_override()):
                self.assertEqual(observation.effective_text_provider(), "kie")

    def test_image_override_does_not_change_text_provider(self):
        image = NodeLlmOverride("img", "stable", "image", "vibecode", "img")
        from app.settings import settings
        with patch.object(type(settings), "text_llm_is_vibecode", new_callable=lambda: property(lambda self: False)):
            with use_override(image):
                self.assertEqual(observation.effective_text_provider(), "kie")
