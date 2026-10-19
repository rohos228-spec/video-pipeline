"""Авария до тела и статус >=400 не списываются. Чанки и пустой 200 — да."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.orchestrator.routing.config import Price
from app.orchestrator.routing.ledger import SpendLedger
from app.orchestrator.routing.model_router import LiveRouter
from app.orchestrator.routing.transport import (
    CompletionResult,
    HttpxTransport,
    ProviderError,
    ProviderRequest,
    ProviderTimeout,
    parse_chat_payload,
)
from app.orchestrator.routing.wire import PAY52_MODULE_MISSING
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


class _EventStream:
    def __init__(self, lines: list[str], exc: BaseException) -> None:
        self.status_code = 200
        self._lines = lines
        self._exc = exc

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        raise self._exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _EventClient:
    def __init__(self, lines: list[str], exc: BaseException) -> None:
        self._lines = lines
        self._exc = exc

    def stream(self, *_args, **_kwargs):
        return _EventStream(self._lines, self._exc)


async def _abort_on(method: str, providers, lines: list[str], exc: BaseException):
    request = _request("привет" * 4)
    client = _EventClient(lines, exc)
    transport = HttpxTransport(providers)
    call = transport._stream_responses if method == "responses" else transport._stream_chat
    if isinstance(exc, httpx.TimeoutException):
        expected = ProviderTimeout
    elif isinstance(exc, httpx.TransportError):
        expected = ProviderError
    else:
        expected = type(exc)
    with pytest.raises(expected) as caught:
        await call(
            client,
            "http://example.test/v1/chat/completions",
            {},
            {},
            request,
        )
    return caught.value


async def test_abort_after_reasoning_chunk_is_charged(config, sink, sleeper):
    priced = _priced(config)
    line = 'data: {"choices":[{"delta":{"reasoning_content":"думаю"}}]}'
    exc = await _abort_on("chat", priced.providers, [line], httpx.ReadTimeout("mid"))
    assert exc.billable is True
    assert exc.partial is not None
    assert exc.partial.text == ""

    prompt = "привет" * 4
    ledger = SpendLedger(internal_budget_usd=10_000)
    transport = FakeTransport([exc, ok("дальше", prompt_tokens=1, completion_tokens=1)])
    router = LiveRouter(priced, transport=transport, ledger=ledger, sink=sink, sleeper=sleeper)
    done = await router.call(
        "strong",
        [{"role": "user", "content": prompt}],
        max_tokens=8,
        run_id="run-reason",
    )
    assert done.text == "дальше"
    assert sink.records[0].status == "timeout"
    assert sink.records[0].cost_usd > 0
    assert ledger.spent("run-reason") > 0

    cancelled = await _abort_on(
        "chat",
        priced.providers,
        [line],
        asyncio.CancelledError(),
    )
    assert cancelled.billable is True
    sink.records.clear()
    router = LiveRouter(
        priced,
        transport=FakeTransport([cancelled]),
        ledger=ledger,
        sink=sink,
        sleeper=sleeper,
    )
    with pytest.raises(asyncio.CancelledError):
        await router.call(
            "strong",
            [{"role": "user", "content": prompt}],
            max_tokens=8,
            run_id="run-cancel-chunk",
        )
    assert ledger.spent("run-cancel-chunk") > 0

    silent = await _abort_on("chat", priced.providers, [], httpx.ReadTimeout("before"))
    assert silent.billable is False


async def test_abort_after_response_created_is_charged(config, sink, sleeper):
    priced = _priced(config)
    line = (
        'data: {"type":"response.created","response":'
        '{"id":"resp_1","status":"in_progress"}}'
    )
    exc = await _abort_on(
        "responses",
        priced.providers,
        [line],
        httpx.ReadTimeout("mid"),
    )
    assert exc.billable is True
    assert exc.partial is not None
    assert exc.partial.text == ""

    ledger = SpendLedger(internal_budget_usd=10_000)
    router = LiveRouter(
        priced,
        transport=FakeTransport([exc, ok("дальше", prompt_tokens=1, completion_tokens=1)]),
        ledger=ledger,
        sink=sink,
        sleeper=sleeper,
    )
    done = await router.call(
        "strong",
        [{"role": "user", "content": "привет"}],
        max_tokens=8,
        run_id="run-created",
    )
    assert done.text == "дальше"
    assert sink.records[0].cost_usd > 0
    assert ledger.spent("run-created") > 0


async def test_usage_without_text_starts_the_body(config, sink, sleeper):
    priced = _priced(config)
    line = (
        'data: {"choices":[{"delta":{}}],'
        '"usage":{"prompt_tokens":3,"completion_tokens":0,"reasoning_tokens":0}}'
    )
    exc = await _abort_on("chat", priced.providers, [line], httpx.ReadTimeout("mid"))
    assert exc.billable is True
    assert exc.partial is not None
    assert exc.partial.text == ""
    assert exc.partial.prompt_tokens == 3

    bare = ProviderError(
        "usage only",
        retryable=True,
        partial=CompletionResult(
            text="",
            finish_reason="",
            model="gpt-5.6-sol",
            prompt_tokens=3,
        ),
    )
    assert bare.billable is True

    ledger = SpendLedger(internal_budget_usd=10_000)
    router = LiveRouter(
        priced,
        transport=FakeTransport([exc, ok("дальше", prompt_tokens=1, completion_tokens=1)]),
        ledger=ledger,
        sink=sink,
        sleeper=sleeper,
    )
    done = await router.call(
        "strong",
        [{"role": "user", "content": "привет" * 20}],
        max_tokens=100,
        run_id="run-usage",
    )
    assert done.text == "дальше"
    assert sink.records[0].usage_estimated is False
    assert sink.records[0].prompt_tokens == 3
    assert sink.records[0].cost_usd == 3


def test_empty_word_alone_is_not_billable():
    noisy = ProviderError("connection pool is empty", retryable=True)
    assert noisy.billable is False
    with pytest.raises(ProviderError) as caught:
        parse_chat_payload({})
    assert caught.value.billable is True
    assert str(caught.value) == "empty chat completion"


async def test_non_json_200_is_charged(config, sink, sleeper):
    priced = _priced(config)

    class _Body:
        status_code = 200

        def json(self):
            raise json.JSONDecodeError("Expecting value", "not-json", 0)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return _Body()

    live = HttpxTransport(
        priced.providers,
        env={"PAY52_GPT_API_KEY": "test", "PAY52_BASE_URL": "https://example.test/v1"},
        client_factory=lambda _timeout: _Client(),
        pay52_resolver=lambda _model: PAY52_MODULE_MISSING,
    )
    request = ProviderRequest(
        model="gpt-5.6-sol",
        provider="52pay",
        messages=[{"role": "user", "content": "привет" * 4}],
        temperature=None,
        max_tokens=8,
        reasoning_effort="high",
        timeout_s=30,
        stream=False,
    )
    with pytest.raises(ProviderError) as caught:
        await live.complete(request)
    assert caught.value.billable is True

    ledger = SpendLedger(internal_budget_usd=10_000)
    router = LiveRouter(
        priced,
        transport=FakeTransport(
            [caught.value, ok("дальше", prompt_tokens=1, completion_tokens=1)]
        ),
        ledger=ledger,
        sink=sink,
        sleeper=sleeper,
    )
    done = await router.call(
        "strong",
        [{"role": "user", "content": "привет" * 4}],
        max_tokens=8,
        run_id="run-json",
    )
    assert done.text == "дальше"
    assert sink.records[0].cost_usd > 0
    assert ledger.spent("run-json") > 0
