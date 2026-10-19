"""ReplayRouter: hash-ключ, fixture-miss не становится живым вызовом."""

from __future__ import annotations

import json

import pytest

from app.orchestrator.routing.hashing import fixture_key
from app.orchestrator.routing.model_router import primary_key_fields
from app.orchestrator.routing.replay import ReplayRouter
from app.orchestrator.routing.types import FixtureMiss, LLMResponse, LLMUsage
from tests.routing.fakes import FakeTransport, ok


def _write(dir_path, *, key, text, model="gpt-5.6-sol"):
    body = {
        "key": key,
        "request_meta": {
            "tier": "strong",
            "temperature": None,
            "max_tokens": None,
            "reasoning_effort": "high",
        },
        "response": {
            "text": text,
            "call_id": "recorded-call",
            "model": model,
            "provider": "52pay",
            "tier": "strong",
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 4,
                "reasoning_tokens": 1,
            },
            "cost_usd": 0.5,
            "latency_ms": 20,
            "fallback_index": 0,
            "deterministic": True,
            "fixture_key": key,
        },
        "recorded_at": "2026-10-17T00:00:00Z",
        "recorded_model": model,
    }
    (dir_path / f"{key}.json").write_text(json.dumps(body), encoding="utf-8")
    return body


async def test_fixture_miss_raises_and_does_not_call_transport(config, tmp_path, sink):
    transport = FakeTransport([ok("live")])
    router = ReplayRouter(
        config, fixture_dir=tmp_path, transport=transport, sink=sink
    )
    with pytest.raises(FixtureMiss) as exc:
        await router.call("strong", [{"role": "user", "content": "нет фикстуры"}], run_id="miss")
    assert exc.value.label == "fixture-miss"
    assert "fixture-miss" in str(exc.value)
    assert transport.calls == []
    assert sink.records[-1].status == "fixture_miss"
    assert sink.records[-1].router_impl == "replay"
    assert sink.records[-1].fixture_key


async def test_fixture_hit_returns_recorded_response(config, tmp_path, ledger):
    messages = [{"role": "user", "content": "есть фикстура"}]
    fields = primary_key_fields(
        config,
        tier="strong",
        messages=messages,
        temperature=None,
        max_tokens=None,
    )
    key = fixture_key(**fields)
    _write(tmp_path, key=key, text="записано")
    transport = FakeTransport([ok("live")])
    router = ReplayRouter(
        config, fixture_dir=tmp_path, transport=transport, ledger=ledger
    )
    response = await router.call("strong", messages, run_id="run-r")
    assert isinstance(response, LLMResponse)
    assert response.text == "записано"
    assert response.fixture_key == key
    assert response.call_id != "recorded-call"
    assert response.usage == LLMUsage(3, 4, 1)
    assert response.cost_usd == 0.5
    assert ledger.spent("run-r") == 0.5
    assert transport.calls == []


def test_temperature_whole_number_does_not_change_key():
    messages = [{"role": "user", "content": "a"}]
    a = fixture_key(
        tier="fast",
        messages=messages,
        temperature=1,
        max_tokens=None,
        reasoning_effort=None,
    )
    b = fixture_key(
        tier="fast",
        messages=messages,
        temperature=1.0,
        max_tokens=None,
        reasoning_effort=None,
    )
    c = fixture_key(
        tier="fast",
        messages=messages,
        temperature=0,
        max_tokens=None,
        reasoning_effort=None,
    )
    assert a == b
    assert a != c
    assert len(a) == 64
