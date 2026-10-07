"""W4-5 / C09 (уровень U): усечение ответа LLM (finish_reason=length, status=incomplete).

Сети и платных вызовов нет: HTTP-граница chat/completions подменена. Настоящими остаются gpt_api.chat
(политика усечения), _chat_single, _parse_choice, llm_completion.response_truncated.
Не доказывается: поведение реального провайдера и замер reasoning.effort (шаг F03, уровень L).
"""
import json
from unittest.mock import patch

import httpx
import pytest

from app.services import gpt_api
from app.services.gpt_api import GptApiError, GptChatResult, GptTruncatedError, raised_output_cap


def _payload(text, finish):
    return {"id": "chatcmpl-1", "choices": [{"index": 0, "finish_reason": finish,
                                              "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 9}}


class FakeHttp:
    """Скриптованная граница провайдера: ответы по очереди, тела запросов запоминаются."""

    def __init__(self, replies):
        self.replies, self.bodies = list(replies), []

    def client(self, **_kw):
        outer = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def post(self, url, headers=None, json=None):
                outer.bodies.append(json)
                return httpx.Response(200, json=outer.replies.pop(0), request=httpx.Request("POST", url))
        return _Client()


@pytest.fixture
def provider(monkeypatch):
    holder = {}

    def install(replies):
        fake = FakeHttp(replies)
        holder["fake"] = fake
        return fake

    async def no_dns(_url):
        return None

    class _Paid:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(gpt_api, "is_responses_mode", lambda: False)
    monkeypatch.setattr(gpt_api, "_node_vibecode_override", lambda: False)
    monkeypatch.setattr(gpt_api, "_node_kie_override", lambda: False)
    monkeypatch.setattr(gpt_api, "_headers", lambda: {})
    monkeypatch.setattr(gpt_api, "_chat_url", lambda model: "http://127.0.0.1:1/v1/chat/completions")
    monkeypatch.setattr(gpt_api, "_ensure_url_host_resolves", no_dns)
    monkeypatch.setattr(gpt_api.spend_guard, "paid_call", lambda *a, **k: _Paid())
    monkeypatch.setattr(gpt_api.settings, "gpt_model", "synthetic-model", raising=False)
    holder["install"] = install

    def go(replies):
        fake = install(replies)
        monkeypatch.setattr(gpt_api, "_async_client", fake.client)
        return fake
    return go


async def _chat(**kw):
    with patch.object(gpt_api.settings.__class__, "text_llm_is_vibecode", False, create=True):
        return await gpt_api.chat(prompt="p", auto_pack=False, max_retries=0, model="synthetic-model", **kw)


async def test_complete_answer_is_untouched_one_request(provider):
    fake = provider([_payload("{}", "stop")])
    result = await _chat()
    assert result.text == "{}" and len(fake.bodies) == 1
    assert "max_tokens" not in fake.bodies[0]
    assert "truncation" not in (result.raw or {})


async def test_idempotent_text_step_retries_once_with_raised_limit(provider):
    fake = provider([_payload("кусок", "length"), _payload("полный ответ", "stop")])
    result = await _chat(idempotent_text=True, max_output_tokens=1000)
    assert result.text == "полный ответ"
    assert [b.get("max_tokens") for b in fake.bodies] == [1000, 2000]


async def test_idempotent_retry_without_known_cap_sets_the_default_raised_cap(provider):
    fake = provider([_payload("кусок", "length"), _payload("ok", "stop")])
    await _chat(idempotent_text=True)
    assert [b.get("max_tokens") for b in fake.bodies] == [None, gpt_api.TRUNCATION_RETRY_DEFAULT_TOKENS]


async def test_still_truncated_after_the_one_retry_is_an_explicit_incident_no_third_request(provider):
    fake = provider([_payload("a", "length"), _payload("ab", "length"), _payload("never", "stop")])
    with pytest.raises(GptTruncatedError) as caught:
        await _chat(idempotent_text=True, max_output_tokens=500)
    assert len(fake.bodies) == 2, "ровно один повтор"
    ctx = caught.value.context
    assert (ctx["error_kind"], ctx["retried"], ctx["retry_output_cap"], ctx["output_cap"]) == ("truncated", True, 1000, 500)
    assert ctx["retryable"] is False and ctx["partial_text"] == "ab"


async def test_non_idempotent_step_never_repeats_the_paid_call_it_gets_an_event(provider):
    fake = provider([_payload("половина", "length"), _payload("лишний платный", "stop")])
    result = await _chat(max_output_tokens=500)
    assert len(fake.bodies) == 1, "не идемпотентный шаг: повторного платного запроса нет"
    assert result.text == "половина"
    marker = result.raw["truncation"]
    assert (marker["outcome"], marker["retried"], marker["finish_reason"]) == ("event_no_retry", False, "length")


async def test_empty_answer_with_length_reasoning_ate_the_cap_is_incident_for_non_idempotent(provider):
    fake = provider([_payload("", "length")])
    with pytest.raises(GptTruncatedError) as caught:
        await _chat(max_output_tokens=800)
    assert len(fake.bodies) == 1 and caught.value.context["partial_chars"] == 0
    assert isinstance(caught.value, GptApiError) and caught.value.retryable is False


async def test_empty_answer_with_length_gets_one_retry_when_idempotent(provider):
    fake = provider([_payload("", "length"), _payload("{\"ok\": true}", "stop")])
    result = await _chat(idempotent_text=True, max_output_tokens=800)
    assert json.loads(result.text) == {"ok": True}
    assert [b.get("max_tokens") for b in fake.bodies] == [800, 1600]


async def test_cap_already_at_maximum_cannot_be_raised_so_incident_without_retry(provider):
    fake = provider([_payload("x", "length")])
    with pytest.raises(GptTruncatedError) as caught:
        await _chat(idempotent_text=True, max_output_tokens=gpt_api.TRUNCATION_RETRY_MAX_TOKENS)
    assert len(fake.bodies) == 1 and caught.value.context["retried"] is False


async def test_other_empty_answer_error_is_not_mistaken_for_truncation(provider):
    provider([_payload("", "stop")])
    with pytest.raises(GptApiError) as caught:
        await _chat(idempotent_text=True)
    assert not isinstance(caught.value, GptTruncatedError)


def test_raised_output_cap_rules():
    assert raised_output_cap(None) == gpt_api.TRUNCATION_RETRY_DEFAULT_TOKENS
    assert raised_output_cap(1000) == 2000
    assert raised_output_cap(50000) == gpt_api.TRUNCATION_RETRY_MAX_TOKENS
    assert raised_output_cap(gpt_api.TRUNCATION_RETRY_MAX_TOKENS) is None


async def test_responses_status_incomplete_is_truncation_too(monkeypatch):
    """Responses API: status=incomplete (а не finish_reason) тоже усечение; повтор идёт с max_output_tokens."""
    calls = []

    async def fake_single(**kw):
        calls.append(kw["output_cap"])
        if len(calls) == 1:
            return GptChatResult(text="обрыв", model="m", finish_reason="incomplete", raw={"status": "incomplete"})
        return GptChatResult(text="целый", model="m", finish_reason="completed", raw={"status": "completed"})

    monkeypatch.setattr(gpt_api, "_chat_single", fake_single)
    result = await gpt_api.chat(prompt="p", auto_pack=False, idempotent_text=True, max_output_tokens=300)
    assert result.text == "целый" and calls == [300, 600]


async def test_body_field_name_follows_the_api_family(monkeypatch):
    """Responses-режим кладёт max_output_tokens, chat/completions - max_tokens."""
    seen = {}

    async def fake_stream(**kw):
        seen["body"] = kw["body"]
        return GptChatResult(text="ok", model="m", finish_reason="completed", raw={"status": "completed"})

    async def no_dns(_url):
        return None

    monkeypatch.setattr(gpt_api, "is_responses_mode", lambda: True)
    monkeypatch.setattr(gpt_api, "_node_vibecode_override", lambda: False)
    monkeypatch.setattr(gpt_api, "_node_kie_override", lambda: False)
    monkeypatch.setattr(gpt_api, "_headers", lambda: {})
    monkeypatch.setattr(gpt_api, "_chat_url", lambda model: "http://127.0.0.1:1/v1/responses")
    monkeypatch.setattr(gpt_api, "_ensure_url_host_resolves", no_dns)
    monkeypatch.setattr(gpt_api, "_chat_responses_stream", fake_stream)
    await gpt_api.chat(prompt="p", auto_pack=False, max_output_tokens=1234, model="m")
    assert seen["body"]["max_output_tokens"] == 1234 and "max_tokens" not in seen["body"]


def test_incomplete_finish_reason_counts_as_truncation_and_stop_does_not():
    from app.services.llm_completion import response_complete, response_truncated
    assert response_truncated({}, "incomplete") and not response_complete({}, "incomplete")
    assert not response_truncated({}, "stop") and response_complete({}, "stop")
    assert not response_truncated({"choices": [{"finish_reason": "content_filter"}]})
