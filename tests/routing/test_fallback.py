"""Таймаут: 2 ретрая с бэкоффом, затем следующая модель."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from app.orchestrator.routing.model_router import LiveRouter, RouterExhausted
from app.orchestrator.routing.transport import ProviderError, ProviderTimeout
from tests.routing.fakes import FakeTransport, ok


async def test_timeout_falls_through_to_next_model(config, sleeper, sink, ledger):
    transport = FakeTransport(
        [ProviderTimeout("slow")] * 3 + [ok("kie-ok", model="gpt-5.6-sol")]
    )
    router = LiveRouter(
        config, transport=transport, sleeper=sleeper, sink=sink, ledger=ledger
    )
    response = await router.call(
        "strong",
        [{"role": "user", "content": "привет"}],
        run_id="run-fb",
        task_id="task-1",
        agent_role="planner",
    )
    assert response.text == "kie-ok"
    assert response.fallback_index == 1
    assert response.provider == "kie"
    assert response.model == "gpt-5.6-sol"
    assert [c.provider for c in transport.calls] == ["52pay", "52pay", "52pay", "kie"]
    assert [c.model for c in transport.calls] == ["gpt-5.6-sol"] * 4
    assert all(c.timeout_s == 300 for c in transport.calls)
    assert all(c.reasoning_effort == "high" for c in transport.calls)
    assert sleeper.calls == [1.0, 2.0]
    assert [row.status for row in sink.records] == ["timeout", "timeout", "timeout", "ok"]
    assert [row.attempt for row in sink.records] == [1, 2, 3, 1]
    assert [row.fallback_index for row in sink.records] == [0, 0, 0, 1]
    assert len({row.call_id for row in sink.records}) == 4
    assert sink.records[-1].router_impl == "live"
    assert sink.records[-1].agent_role == "planner"
    assert sink.records[-1].requested_tier == "strong"
    assert sink.records[-1].tier == "strong"
    assert sink.records[-1].budget_scope == "internal"
    assert sink.records[-1].deterministic_flag is True
    assert "привет" not in repr(sink.records[-1])
    assert response.call_id == sink.records[-1].call_id
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", sink.records[-1].created_at
    )


async def test_fast_timeout_uses_60s_and_haiku(config, sleeper):
    flagged = replace(config, d6_confirmed=True)
    transport = FakeTransport(
        [ProviderTimeout("glm")] * 3 + [ok("haiku", model="claude-haiku")]
    )
    router = LiveRouter(flagged, transport=transport, sleeper=sleeper)
    response = await router.call("fast", [{"role": "user", "content": "да"}])
    assert response.model == "claude-haiku"
    assert response.provider == "52pay"
    assert response.fallback_index == 1
    assert transport.calls[0].model == "glm-5.3-flash"
    assert transport.calls[0].timeout_s == 60
    assert transport.calls[0].reasoning_effort is None
    assert transport.calls[-1].model == "claude-haiku"
    assert transport.calls[-1].timeout_s == 60
    assert sleeper.calls == [1.0, 2.0]


async def test_fatal_status_skips_retries(config, sleeper):
    transport = FakeTransport(
        [
            ProviderError("unauthorized", retryable=False, status_code=401),
            ok("kie", model="gpt-5.6-sol"),
        ]
    )
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    response = await router.call("strong", [{"role": "user", "content": "hi"}])
    assert response.provider == "kie"
    assert len(transport.calls) == 2
    assert sleeper.calls == []


async def test_exhausted_chain_raises(config, sleeper):
    transport = FakeTransport([ProviderTimeout("x")] * 6)
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    with pytest.raises(RouterExhausted):
        await router.call("strong", [{"role": "user", "content": "hi"}])
