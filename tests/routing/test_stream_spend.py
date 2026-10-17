"""Стрим без usage не обходит лимит: оценка > 0 и тот же отказ до вызова."""

from __future__ import annotations

from dataclasses import replace

from app.orchestrator.routing.config import Price
from app.orchestrator.routing.ledger import SpendLedger
from app.orchestrator.routing.model_router import LiveRouter
from app.orchestrator.routing.transport import ProviderError, ProviderRequest, build_body
from tests.routing.fakes import FakeTransport, ok


def _strong_completion(config):
    prices = dict(config.prices)
    prices["gpt-5.6-sol"] = Price(prompt=0, completion=1_000_000, reasoning=0)
    return replace(config, prices=prices)


def test_chat_stream_requests_include_usage():
    request = ProviderRequest(
        model="gpt-5.6-sol",
        provider="52pay",
        messages=[{"role": "user", "content": "да"}],
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        timeout_s=30,
        stream=True,
    )
    body = build_body(request, "openai-chat")
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    quiet = build_body(
        ProviderRequest(
            model="gpt-5.6-sol",
            provider="52pay",
            messages=[{"role": "user", "content": "да"}],
            temperature=None,
            max_tokens=None,
            reasoning_effort=None,
            timeout_s=30,
            stream=False,
        ),
        "openai-chat",
    )
    assert "stream_options" not in quiet


async def test_stream_without_usage_charges_the_ledger(config):
    priced = _strong_completion(config)
    transport = FakeTransport([ok("привет", prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)])
    ledger = SpendLedger(internal_budget_usd=10)
    router = LiveRouter(priced, transport=transport, ledger=ledger)
    response = await router.call(
        "strong",
        [{"role": "user", "content": "да"}],
        stream=True,
        run_id="run-stream",
    )
    assert response.text == "привет"
    assert response.usage.prompt_tokens == (len("да") + 1) // 2
    assert response.usage.completion_tokens == (len("привет") + 1) // 2
    assert response.usage.reasoning_tokens == response.usage.completion_tokens
    assert response.cost_usd == response.usage.completion_tokens
    assert response.cost_usd > 0
    assert ledger.spent("run-stream") == response.cost_usd
    assert transport.calls[0].stream is True


async def test_nonstream_without_usage_is_not_free(config):
    priced = _strong_completion(config)
    transport = FakeTransport([ok("аб", prompt_tokens=0, completion_tokens=0)])
    ledger = SpendLedger(internal_budget_usd=10)
    router = LiveRouter(priced, transport=transport, ledger=ledger)
    response = await router.call(
        "strong",
        [{"role": "user", "content": "да"}],
        stream=False,
        run_id="run-plain",
    )
    assert response.usage.prompt_tokens == (len("да") + 1) // 2
    assert response.usage.completion_tokens >= (len("аб") + 1) // 2
    assert response.cost_usd > 0
    assert ledger.spent("run-plain") == response.cost_usd


async def test_missing_usage_charges_the_prompt(config, sink):
    prices = dict(config.prices)
    prices["gpt-5.6-sol"] = Price(prompt=1_000_000, completion=1_000_000, reasoning=1_000_000)
    priced = replace(config, prices=prices)
    prompt = "привет" * 20
    text = "абв"
    transport = FakeTransport(
        [ok(text, prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)]
    )
    ledger = SpendLedger(internal_budget_usd=10_000)
    router = LiveRouter(priced, transport=transport, ledger=ledger, sink=sink)
    response = await router.call(
        "strong",
        [{"role": "user", "content": prompt}],
        max_tokens=10,
        stream=True,
        run_id="run-est",
    )
    prompt_tokens = (len(prompt) + 1) // 2
    completion_tokens = max((len(text) + 1) // 2, 10)
    assert response.usage.prompt_tokens == prompt_tokens
    assert response.usage.completion_tokens == completion_tokens
    assert response.usage.reasoning_tokens == completion_tokens
    assert response.cost_usd == prompt_tokens + completion_tokens + completion_tokens
    assert response.cost_usd > completion_tokens
    assert ledger.spent("run-est") == response.cost_usd
    assert sink.records[-1].usage_estimated is True
    assert sink.records[-1].prompt_tokens == prompt_tokens


async def test_empty_reply_and_retry_are_charged(config, sink, sleeper):
    prices = dict(config.prices)
    prices["gpt-5.6-sol"] = Price(prompt=1_000_000, completion=1_000_000, reasoning=1_000_000)
    priced = replace(config, prices=prices)
    prompt = "привет" * 4
    prompt_tokens = (len(prompt) + 1) // 2
    ledger = SpendLedger(internal_budget_usd=10_000)
    blank = FakeTransport(
        [ok("   ", prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, finish_reason="length")]
    )
    router = LiveRouter(priced, transport=blank, ledger=ledger, sink=sink, sleeper=sleeper)
    messages = [{"role": "user", "content": prompt}]
    empty = await router.call("strong", messages, max_tokens=8, run_id="run-empty")
    assert empty.text.strip() == ""
    assert empty.usage.prompt_tokens == prompt_tokens
    assert empty.usage.completion_tokens == 8
    assert empty.usage.reasoning_tokens == 8
    assert empty.cost_usd == prompt_tokens + 16
    assert ledger.spent("run-empty") == empty.cost_usd

    retry = FakeTransport(
        [
            ProviderError("empty chat completion", retryable=True),
            ok("готово", prompt_tokens=1, completion_tokens=1),
        ]
    )
    sink.records.clear()
    router = LiveRouter(priced, transport=retry, ledger=ledger, sink=sink, sleeper=sleeper)
    done = await router.call("strong", messages, max_tokens=8, run_id="run-empty")
    assert done.text == "готово"
    assert len(retry.calls) == 2
    assert sink.records[0].status == "error"
    assert sink.records[0].usage_estimated is True
    assert sink.records[0].cost_usd == prompt_tokens + 16
    assert ledger.spent("run-empty") == empty.cost_usd + sink.records[0].cost_usd + done.cost_usd


async def test_exhausted_budget_blocks_stream_before_transport(config):
    ledger = SpendLedger(internal_budget_usd=10)
    ledger.hydrate("run-stream", 10)
    transport = FakeTransport([ok("не должен уйти")])
    router = LiveRouter(config, transport=transport, ledger=ledger)
    response = await router.call(
        "strong",
        [{"role": "user", "content": "да"}],
        stream=True,
        run_id="run-stream",
    )
    assert response.text == ""
    assert response.task_status == "waiting_approval"
    assert response.approval is not None
    assert response.approval.reason == "budget_exhausted"
    assert transport.calls == []
