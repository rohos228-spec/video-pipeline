"""52pay берёт маршрут из resolve_text, Kie — слаг settings.gpt_model и stream."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from app.orchestrator.routing.model_router import LiveRouter
from app.orchestrator.routing.transport import (
    HttpxTransport,
    ProviderError,
    ProviderRequest,
)
from app.orchestrator.routing.wire import (
    PAY52_MODULE_MISSING,
    kie_model_slug,
    pay52_base_url,
    resolve_pay52,
)
from tests.routing.fakes import FakeTransport, ok


def _request(provider: str, model: str, *, stream: bool = False) -> ProviderRequest:
    return ProviderRequest(
        model=model,
        provider=provider,
        messages=[{"role": "user", "content": "hi"}],
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        timeout_s=30,
        stream=stream,
    )


class _PostResponse:
    status_code = 200

    def json(self):
        return {
            "model": "gpt-5.6-sol",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


class _PostClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        return _PostResponse()


class _StreamBody:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _StreamClient:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.streams: list[dict] = []
        self.posts: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "json": json})
        raise AssertionError("kie fallback must stream")

    def stream(self, method, url, headers=None, json=None):
        self.streams.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        return _StreamBody(self.lines)


def test_pay52_base_url_reads_settings(monkeypatch):
    import app.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "settings",
        SimpleNamespace(pay52_base_url="https://from-settings.example/v1/"),
    )
    monkeypatch.delenv("PAY52_BASE_URL", raising=False)
    assert pay52_base_url() == "https://from-settings.example/v1"
    assert "api.52pay.com" not in pay52_base_url()


def test_kie_slug_reads_gpt_model(monkeypatch):
    import app.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "settings",
        SimpleNamespace(gpt_model="gpt-5-6-sol"),
    )
    monkeypatch.delenv("GPT_MODEL", raising=False)
    assert kie_model_slug() == "gpt-5-6-sol"


def test_resolve_pay52_calls_existing_resolve_text(monkeypatch):
    module = types.ModuleType("app.services.pay52_routes")

    def resolve_text(model: str):
        assert model == "gpt-5-6-sol"
        return SimpleNamespace(
            api_model="gpt-5.6-sol",
            api_key="from-pay52-gpt-api-key",
            url="https://billing.example/v1/chat/completions",
        )

    module.resolve_text = resolve_text
    monkeypatch.setitem(sys.modules, "app.services.pay52_routes", module)
    route = resolve_pay52("gpt-5-6-sol")
    assert route.api_model == "gpt-5.6-sol"
    assert route.api_key == "from-pay52-gpt-api-key"
    assert route.url == "https://billing.example/v1/chat/completions"


def test_resolve_pay52_missing_module_is_explicit():
    route = resolve_pay52("gpt-5.6-sol")
    if route is PAY52_MODULE_MISSING:
        return
    assert route is None or getattr(route, "api_model", "")


async def test_pay52_http_uses_resolver_url_not_hardcoded_host(config):
    client = _PostClient()

    def resolver(model: str):
        assert model == "gpt-5.6-sol"
        return SimpleNamespace(
            api_model="gpt-5.6-sol",
            api_key="route-key",
            url="https://billing.example/v1/chat/completions",
        )

    transport = HttpxTransport(
        config.providers,
        env={},
        client_factory=lambda timeout: client,
        pay52_resolver=resolver,
    )
    result = await transport.complete(_request("52pay", "gpt-5.6-sol"))
    assert result.text == "ok"
    assert client.posts[0]["url"] == "https://billing.example/v1/chat/completions"
    assert "api.52pay.com" not in client.posts[0]["url"]
    assert client.posts[0]["headers"]["Authorization"] == "Bearer route-key"
    assert client.posts[0]["json"]["model"] == "gpt-5.6-sol"


async def test_pay52_http_base_comes_from_settings_without_module(config, monkeypatch):
    import app.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "settings",
        SimpleNamespace(pay52_base_url="https://from-settings.example/v1"),
    )
    monkeypatch.delenv("PAY52_BASE_URL", raising=False)
    client = _PostClient()
    transport = HttpxTransport(
        config.providers,
        env={"PAY52_GPT_API_KEY": "env-key"},
        client_factory=lambda timeout: client,
        pay52_resolver=lambda model: PAY52_MODULE_MISSING,
    )
    await transport.complete(_request("52pay", "gpt-5.6-sol"))
    assert client.posts[0]["url"] == "https://from-settings.example/v1/chat/completions"
    assert client.posts[0]["headers"]["Authorization"] == "Bearer env-key"


async def test_pay52_route_miss_does_not_post(config):
    client = _PostClient()
    transport = HttpxTransport(
        config.providers,
        env={"PAY52_GPT_API_KEY": "env-key"},
        client_factory=lambda timeout: client,
        pay52_resolver=lambda model: None,
    )
    with pytest.raises(ProviderError) as caught:
        await transport.complete(_request("52pay", "unknown-model"))
    assert caught.value.retryable is False
    assert client.entered is False
    assert client.posts == []


async def test_kie_http_is_stream_and_uses_gpt_model(config, monkeypatch):
    import app.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "settings",
        SimpleNamespace(gpt_model="gpt-5-6-sol"),
    )
    lines = [
        'data: {"type":"response.output_text.delta","delta":"резерв"}',
        'data: {"type":"response.completed","response":{"model":"gpt-5-6-sol","status":"completed","usage":{"input_tokens":1,"output_tokens":1,"output_tokens_details":{"reasoning_tokens":0}}}}',
    ]
    client = _StreamClient(lines)
    transport = HttpxTransport(
        config.providers,
        env={"GPT_API_KEY": "kie-key", "GPT_BASE_URL": "https://api.kie.ai"},
        client_factory=lambda timeout: client,
    )
    request = _request("kie", "gpt-5.6-sol", stream=False)
    result = await transport.complete(request)
    assert result.text == "резерв"
    assert result.model == "gpt-5-6-sol"
    assert client.posts == []
    assert len(client.streams) == 1
    body = client.streams[0]["json"]
    assert body["model"] == "gpt-5-6-sol"
    assert body["stream"] is True
    assert client.streams[0]["url"] == "https://api.kie.ai/codex/v1/responses"


async def test_router_kie_fallback_streams_even_if_caller_does_not(config, sleeper, monkeypatch):
    import app.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "settings",
        SimpleNamespace(gpt_model="gpt-5-6-sol"),
    )
    from app.orchestrator.routing.transport import ProviderTimeout

    transport = FakeTransport([ProviderTimeout("slow")] * 3 + [ok("kie", model="gpt-5-6-sol")])
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    response = await router.call(
        "strong",
        [{"role": "user", "content": "длинный план"}],
        stream=False,
    )
    kie = transport.calls[-1]
    assert kie.provider == "kie"
    assert kie.model == "gpt-5-6-sol"
    assert kie.stream is True
    assert response.provider == "kie"
    assert all(call.stream is False for call in transport.calls if call.provider == "52pay")
