"""ScriptedRouter отдаёт сценарный ответ и не зовёт провайдера."""

from __future__ import annotations

import pytest

from app.orchestrator.routing.model_router import RouterError
from app.orchestrator.routing.scripted import ScriptedRouter
from tests.routing.fakes import FakeTransport, ok


async def test_scripted_matches_user_text(config, sink):
    transport = FakeTransport([ok("live")])
    router = ScriptedRouter(
        config,
        scripts=[
            {"contains": "план", "text": "PLAN-1", "model": "gpt-5.6-sol"},
            {"regex": r"^да$", "text": "ACK"},
        ],
        transport=transport,
        sink=sink,
    )
    plan = await router.call(
        "strong",
        [{"role": "user", "content": "составь план"}],
        agent_role="planner",
        run_id="s1",
    )
    assert plan.text == "PLAN-1"
    assert plan.model == "gpt-5.6-sol"
    ack = await router.call("fast", [{"role": "user", "content": "да"}], run_id="s2")
    assert ack.text == "ACK"
    assert ack.tier == "strong"
    assert transport.calls == []
    assert {row.router_impl for row in sink.records} == {"scripted"}
    assert sink.records[0].provider == "52pay"


async def test_scripted_miss_is_not_a_live_call(config):
    transport = FakeTransport([ok("live")])
    router = ScriptedRouter(
        config,
        scripts=[{"contains": "план", "text": "x"}],
        transport=transport,
    )
    with pytest.raises(RouterError):
        await router.call("strong", [{"role": "user", "content": "просто так"}], run_id="s-miss")
    assert transport.calls == []


def test_scripted_provider_must_be_52pay_or_kie(config):
    with pytest.raises(ValueError, match="52pay\\|kie"):
        ScriptedRouter(
            config,
            scripts=[{"contains": "план", "text": "x", "provider": "openai"}],
        )


async def test_scripted_classify_is_deterministic_without_script(config):
    router = ScriptedRouter(config, scripts=[{"contains": "x", "text": "y"}])
    decision = await router.classify("Собери отчёт")
    assert decision.tier == "strong"
    assert decision.deterministic is True
