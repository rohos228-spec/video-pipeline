"""D1/D6 по умолчанию выключены: fast-tier молчит, strong = gpt-5.6-sol high."""

from __future__ import annotations

from dataclasses import replace

from app.orchestrator.routing.config import load_config
from app.orchestrator.routing.model_router import LiveRouter
from tests.routing.fakes import FakeTransport, ok


def test_yaml_defaults_are_unconfirmed():
    config = load_config(apply_env=False)
    assert config.d1_confirmed is False
    assert config.d6_confirmed is False
    assert config.fast_timeout_s == 60
    assert config.strong_timeout_s == 300
    assert config.retries == 2
    assert config.internal_budget_usd == 10
    assert config.classifier_threshold == 0.8


async def test_default_fast_request_goes_strong_high(config):
    transport = FakeTransport([ok("s")])
    router = LiveRouter(config, transport=transport)
    response = await router.call("fast", [{"role": "user", "content": "да"}], run_id="run-fast")
    assert response.tier == "strong"
    assert response.model == "gpt-5.6-sol"
    assert response.provider == "52pay"
    hop = transport.calls[0]
    assert hop.model == "gpt-5.6-sol"
    assert hop.reasoning_effort == "high"
    assert hop.timeout_s == 300
    assert hop.provider == "52pay"


async def test_d6_enables_glm_without_high(config):
    transport = FakeTransport([ok("f", model="glm-5.3-flash")])
    router = LiveRouter(replace(config, d6_confirmed=True), transport=transport)
    response = await router.call("fast", [{"role": "user", "content": "да"}], run_id="run-d6")
    assert response.tier == "fast"
    assert response.model == "glm-5.3-flash"
    hop = transport.calls[0]
    assert hop.reasoning_effort is None
    assert hop.timeout_s == 60


async def test_d1_switches_strong_primary(config):
    off = FakeTransport([ok("sol")])
    router = LiveRouter(config, transport=off)
    await router.call("strong", [{"role": "user", "content": "план"}], run_id="run-d1-off")
    assert off.calls[0].model == "gpt-5.6-sol"
    assert off.calls[0].provider == "52pay"

    on = FakeTransport([ok("fable", model="claude-fable-5")])
    router = LiveRouter(replace(config, d1_confirmed=True), transport=on)
    response = await router.call("strong", [{"role": "user", "content": "план"}], run_id="run-d1-on")
    assert response.model == "claude-fable-5"
    assert on.calls[0].reasoning_effort == "high"
    assert on.calls[0].provider == "52pay"


async def test_classify_keeps_logical_fast_when_d6_off(config):
    router = LiveRouter(config, transport=FakeTransport([]))
    decision = await router.classify("суммируй")
    assert decision.tier == "fast"
    assert decision.effective_tier == "strong"
    assert decision.deterministic is True


def test_env_overrides_flags(monkeypatch):
    monkeypatch.setenv("ORCH_D1_CONFIRMED", "1")
    monkeypatch.setenv("ORCH_D6_CONFIRMED", "true")
    config = load_config(apply_env=True)
    assert config.d1_confirmed is True
    assert config.d6_confirmed is True
