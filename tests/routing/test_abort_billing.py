"""Авария до тела и статус >=400 не списываются. Чанки и пустой 200 — да."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app.orchestrator.routing.config import Price
from app.orchestrator.routing.ledger import SpendLedger
from app.orchestrator.routing.model_router import LiveRouter
from app.orchestrator.routing.transport import (
    HttpxTransport,
    ProviderError,
    ProviderRequest,
)
from tests.routing.fakes import FakeTransport, ok


def _priced(config):
    prices = dict(config.prices)
    prices["gpt-5.6-sol"] = Price(prompt=1_000_000, completion=1_000_000, reasoning=1_000_000)
    return replace(config, prices=prices)


def _request(prompt: str) -> ProviderRequest:
    return ProviderRequest(
        model="gpt-5.6-sol",
        provider="52pay",
        messages=[{"role": "user", "content": prompt}],
        temperature=None,
        max_tokens=100,
        reasoning_effort="high",
        timeout_s=30,
        stream=True,
    )


class _Status:
    def __init__(self, code: int) -> None:
        self.status_code = code

    async def aiter_lines(self):
        if False:
            yield ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Client:
    def __init__(self, action) -> None:
        self._action = action

    def stream(self, *_args, **_kwargs):
        return self._action()


async def test_connect_and_http_status_do_not_charge(config, sink, sleeper):
    priced = _priced(config)
    prompt = "привет" * 20
    messages = [{"role": "user", "content": prompt}]
    errors = [
        ProviderError("ConnectError", retryable=True),
        ProviderError("http 429", retryable=True, status_code=429),
        ProviderError("http 503", retryable=True, status_code=503),
    ]
    for exc in errors:
        transport = FakeTransport([exc, exc, exc, ok("kie-ok", model="gpt-5-6-sol", prompt_tokens=1, completion_tokens=1)])
        ledger = SpendLedger(internal_budget_usd=10)
        router = LiveRouter(priced, transport=transport, ledger=ledger, sink=sink, sleeper=sleeper)
        response = await router.call("strong", messages, max_tokens=100, run_id="run-fail")
        assert response.text == "kie-ok"
        assert response.provider == "kie"
        assert response.task_status is None
        assert [call.provider for call in transport.calls] == ["52pay", "52pay", "52pay", "kie"]
        assert all(row.cost_usd == 0 for row in sink.records if row.status == "error")
        assert ledger.spent("run-fail") == response.cost_usd
        assert ledger.spent("run-fail") < 10
        sink.records.clear()


async def test_stream_4xx_and_5xx_do_not_charge(config, sink, sleeper):
    priced = _priced(config)
    prompt = "привет" * 20
    request = _request(prompt)
    for code in (400, 503):
        with pytest.raises(ProviderError) as caught:
            await HttpxTransport(priced.providers)._stream_chat(
                _Client(lambda code=code: _Status(code)),
                "http://example.test/v1/chat/completions",
                {},
                {},
                request,
            )
        assert caught.value.status_code == code
        assert caught.value.billable is False

    class _Raise:
        def stream(self, *_args, **_kwargs):
            raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError) as connect:
        await HttpxTransport(priced.providers)._stream_chat(
            _Raise(),
            "http://example.test/v1/chat/completions",
            {},
            {},
            request,
        )
    assert connect.value.billable is False

    transport = FakeTransport(
        [
            ProviderError("http 503", retryable=True, status_code=503),
            ok("дальше", prompt_tokens=1, completion_tokens=1),
        ]
    )
    ledger = SpendLedger(internal_budget_usd=10)
    router = LiveRouter(priced, transport=transport, ledger=ledger, sink=sink, sleeper=sleeper)
    response = await router.call(
        "strong",
        [{"role": "user", "content": prompt}],
        max_tokens=100,
        stream=True,
        run_id="run-stream-err",
    )
    assert response.text == "дальше"
    assert response.task_status is None
    assert sink.records[0].status == "error"
    assert sink.records[0].cost_usd == 0
    assert ledger.spent("run-stream-err") == response.cost_usd


async def test_empty_200_is_still_charged(config, sink, sleeper):
    priced = _priced(config)
    prompt = "привет" * 4
    ledger = SpendLedger(internal_budget_usd=10_000)
    transport = FakeTransport(
        [ok("   ", prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, finish_reason="length")]
    )
    router = LiveRouter(priced, transport=transport, ledger=ledger, sink=sink, sleeper=sleeper)
    response = await router.call(
        "strong",
        [{"role": "user", "content": prompt}],
        max_tokens=8,
        run_id="run-empty-200",
    )
    assert response.text.strip() == ""
    assert response.cost_usd > 0
    assert ledger.spent("run-empty-200") == response.cost_usd
    assert sink.records[-1].usage_estimated is True
