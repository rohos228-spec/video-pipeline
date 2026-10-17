"""Прод-роутер: ленивый ``app.orchestrator.routing`` или FallbackRouter."""

from __future__ import annotations

import importlib
import types

import pytest

from app.orchestrator.agents.critic import RouterCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import get_runtime, set_runtime
from app.orchestrator.agents.stubs import FallbackRouter
from app.orchestrator.agents.wiring import RoutingAdapter
from app.orchestrator.contracts import ClassifyResult, LLMResponse, LLMUsage, MessageMode, PlanStep


def _response(text: str, tier: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        call_id="fake-call",
        model="scripted",
        provider="52pay",
        tier=tier or "strong",
        finish_reason="stop",
        usage=LLMUsage(),
        cost_usd=0.0,
        latency_ms=0,
        fallback_index=0,
        deterministic=True,
        fixture_key=None,
    )


class _Ws5Router:
    """Сигнатуры call/classify как у ModelRouter #359, без сети."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(
        self,
        tier: str = "auto",
        messages: list | None = None,
        stream: bool = False,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        agent_role: str | None = None,
        risk: str | None = None,
        mutating: bool | None = None,
        budget_scope: str | None = None,
        on_delta: object = None,
    ) -> LLMResponse:
        self.calls.append(
            (
                "call",
                {
                    "tier": tier,
                    "messages": messages,
                    "stream": stream,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "run_id": run_id,
                    "task_id": task_id,
                    "agent_role": agent_role,
                    "risk": risk,
                    "mutating": mutating,
                    "budget_scope": budget_scope,
                    "on_delta": on_delta,
                },
            )
        )
        role = agent_role or ""
        text = "approve" if role == "critic" else '{"steps":[]}'
        return _response(text, tier)

    async def classify(
        self,
        message: str,
        *,
        agent_role: str | None = None,
        risk: str | None = None,
        mutating: bool | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
    ):
        self.calls.append(
            (
                "classify",
                {
                    "message": message,
                    "agent_role": agent_role,
                    "risk": risk,
                    "mutating": mutating,
                    "run_id": run_id,
                    "task_id": task_id,
                },
            )
        )
        return types.SimpleNamespace(
            tier="fast",
            mode=MessageMode.append,
            deterministic=True,
            confidence=0.4,
            kill_switch=False,
        )


def test_missing_routing_keeps_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_RUNTIME", raising=False)
    set_runtime(None)
    try:
        runtime = get_runtime()
        assert isinstance(runtime.router, FallbackRouter)
        assert isinstance(runtime.supervisor.planner, RouterPlanner)
        assert isinstance(runtime.supervisor.critic, RouterCritic)
        assert "routing" in runtime.runtime_degraded
    finally:
        set_runtime(None)


def test_routing_import_error_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_RUNTIME", raising=False)
    original_spec = importlib.util.find_spec
    original_import = importlib.import_module

    def _spec(name: str):
        if name == "app.orchestrator.routing":
            return object()
        return original_spec(name)

    def _import(name: str, package: str | None = None):
        if name == "app.orchestrator.routing":
            raise ImportError("routing failed")
        return original_import(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", _spec)
    monkeypatch.setattr(importlib, "import_module", _import)
    set_runtime(None)
    try:
        runtime = get_runtime()
        assert isinstance(runtime.router, FallbackRouter)
        assert "routing" in runtime.runtime_degraded
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_production_planner_uses_routing_call_and_classify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_RUNTIME", raising=False)
    inner = _Ws5Router()
    module = types.ModuleType("app.orchestrator.routing")
    built: list[str] = []

    def build_router(impl: str = "live", **kwargs: object):
        del kwargs
        built.append(impl)
        return inner

    module.build_router = build_router
    module.ModelRouter = object
    module.LiveRouter = object
    module.ScriptedRouter = object
    module.ReplayRouter = object
    module.RouterError = type("RouterError", (Exception,), {})
    module.RouterExhausted = type("RouterExhausted", (module.RouterError,), {})

    def get_spend_ledger():
        raise AssertionError("ledger")

    module.get_spend_ledger = get_spend_ledger
    monkeypatch.setitem(__import__("sys").modules, "app.orchestrator.routing", module)
    set_runtime(None)
    try:
        runtime = get_runtime()
        assert built == ["live"]
        assert isinstance(runtime.router, RoutingAdapter)
        assert "routing" not in runtime.runtime_degraded
        await runtime.supervisor.planner.make_plan("цель", run_id="run-1", task_id=1)
        verdict = await runtime.supervisor.critic.review(
            [PlanStep(action="noop")],
            run_id="run-1",
            task_id=1,
        )
        assert verdict == "approve"
        kinds = [item[0] for item in inner.calls]
        assert kinds == ["call", "call"]
        for _kind, payload in inner.calls:
            assert "reasoning_effort" not in payload
        assert inner.calls[1][1]["agent_role"] == "critic"
        classified = await runtime.router.classify("привет", run_id="run-1", task_id="1")
        assert isinstance(classified, ClassifyResult)
        copied = classified.model_copy(update={"deterministic": False})
        assert copied.deterministic is False
        assert inner.calls[-1][0] == "classify"
        assert all(item[0] != "ledger" for item in inner.calls)
    finally:
        set_runtime(None)
