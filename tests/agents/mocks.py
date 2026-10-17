"""Моки соседних потоков: actions.execute, validators.run, ScriptedRouter."""

from __future__ import annotations

import asyncio
from typing import Any

from app.orchestrator.contracts import (
    ActionResult,
    ClassifyResult,
    LLMResponse,
    LLMUsage,
    MessageMode,
    PlanStep,
    ValidatorResult,
)


class ScriptedRouter:
    """Детерминированные ответы LLM без сети (D7)."""

    def __init__(self) -> None:
        self.delay = 0.0
        self.gate: asyncio.Event | None = None
        self.answer = "быстрый ответ"
        self.mode = MessageMode.append
        self.tier = "fast"
        self.classify_calls = 0
        self.call_count = 0
        self.started = asyncio.Event()
        self.fail_if_called = False
        self.last_call: dict[str, Any] = {}
        self.deltas: list[str] = []
        self.call_delay = 0.0
        self.call_started = asyncio.Event()
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.script: list[str] = []

    async def classify(self, message: str) -> ClassifyResult:
        del message
        self.classify_calls += 1
        self.started.set()
        if self.fail_if_called:
            raise AssertionError("classifier should not be called")
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.gate is not None:
            await self.gate.wait()
        return ClassifyResult(
            tier=self.tier,
            mode=self.mode,
            deterministic=False,
            confidence=0.9,
        )

    async def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        agent_role: str | None = None,
        budget_scope: str | None = None,
        reasoning_effort: str | None = None,
        on_delta: Any = None,
    ) -> LLMResponse:
        self.seen_messages.append([dict(item) for item in messages])
        self.call_count += 1
        self.call_started.set()
        self.last_call = {
            "tier": tier,
            "stream": stream,
            "run_id": run_id,
            "task_id": task_id,
            "agent_role": agent_role,
            "budget_scope": budget_scope,
            "reasoning_effort": reasoning_effort,
            "on_delta": on_delta,
        }
        if self.deltas and on_delta is not None:
            for piece in self.deltas:
                on_delta(piece)
        if self.call_delay:
            await asyncio.sleep(self.call_delay)
        text = self.script.pop(0) if self.script else self.answer
        return LLMResponse(
            text=text,
            call_id="scripted-call",
            model="scripted",
            provider="52pay",
            tier=tier or "fast",
            finish_reason="stop",
            usage=LLMUsage(),
            cost_usd=0.0,
            latency_ms=1,
            fallback_index=0,
            deterministic=True,
            fixture_key="scripted",
        )


class MockActions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.ticks = 0
        self.on_execute = None

    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> ActionResult:
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        self.started.set()
        if self.on_execute is not None:
            await self.on_execute(name, params, task_ctx)
        if params.get("_wait"):
            await self.release.wait()
        elif params.get("_block"):
            await asyncio.sleep(float(params.get("_seconds", 30)))
        ticks = int(params.get("_tick") or 0)
        if ticks:
            dt = float(params.get("_dt", 0.05))
            for _ in range(ticks):
                await asyncio.sleep(dt)
                self.ticks += 1
        return ActionResult(
            ok=True,
            data={"name": name},
            change_id=params.get("_change_id") or f"chg-{name}-{len(self.calls)}",
            cost=float(params.get("_cost", 0) or 0),
        )


class MockValidators:
    async def run(self, name: str, project_id: int, context: dict[str, Any]) -> ValidatorResult:
        del project_id, context
        return ValidatorResult(name=name, passed=True, details="ok", blocking=False)


class FixedPlanner:
    def __init__(self, steps: list[PlanStep]) -> None:
        self.steps = list(steps)

    async def make_plan(self, goal: str, context: str = "") -> list[PlanStep]:
        del goal, context
        return list(self.steps)


class ScriptedCritic:
    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)

    async def review(self, plan: list[PlanStep], context: str = "") -> str:
        del plan, context
        return self.verdicts.pop(0)
