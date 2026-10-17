"""Заглушки planner / critic / executor / роутера до R2–R3.

Реальных LLM и платных действий нет (D7, R0b ещё не в main).
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.contracts import (
    ORCH_DEFAULT_TIER,
    ActionResult,
    ClassifyResult,
    LLMResponse,
    MessageMode,
    PlanStep,
    Risk,
    ValidatorResult,
)


class StubPlanner:
    async def make_plan(self, goal: str, context: str = "") -> list[PlanStep]:
        return [
            PlanStep(
                action="noop",
                params={"goal": goal, "context": context},
                expected="ok",
                paid=False,
                mutating=False,
            )
        ]


class StubCritic:
    async def review(self, plan: list[PlanStep], context: str = "") -> str:
        del plan, context
        return "approve"


class ActionBackedExecutor:
    """Шаг плана → ``actions.execute``. До WS2 подставляется ``UnlinkedActions``."""

    def __init__(self, actions: Any) -> None:
        self.actions = actions

    async def run(self, step: PlanStep, ctx: dict[str, Any]) -> ActionResult:
        result = await self.actions.execute(step.action, dict(step.params), dict(ctx))
        if isinstance(result, ActionResult):
            return result
        return ActionResult.model_validate(result)


class UnlinkedActions:
    """Мок-заглушка, пока WS2 не отдал ``actions.execute``.

    ``risk_of`` здесь ``none``: реестра ещё нет, и stub-план ``noop`` не
    должен вставать на approval. Боевой WS2 для неизвестного имени
    возвращает ``Risk.high`` (``registry_risk``).
    """

    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> ActionResult:
        del params, task_ctx
        return ActionResult(
            ok=False,
            data={"error": "actions.execute не подключён (ждёт WS2)", "action": name},
            change_id=None,
            cost=0.0,
        )

    def risk_of(self, action_name: str) -> Risk:
        del action_name
        return Risk.none


class UnlinkedValidators:
    """Мок-заглушка, пока WS1 не отдал ``validators.run``. Сигнатура синхронная."""

    def run(self, name: str, project_id: int, context: dict[str, Any]) -> ValidatorResult:
        del project_id, context
        return ValidatorResult(name=name, passed=True, details="validators.run не подключён (ждёт WS1)", blocking=False)


class FallbackRouter:
    """Роутер-заглушка до WS5. Сеть не вызывает.

    До D6 спорная классификация — append и tier ``strong``.
    ``on_delta`` принимается и не вызывается: стрим из готового текста — заглушка очереди.
    """

    async def classify(self, message: str) -> ClassifyResult:
        del message
        return ClassifyResult(
            tier=ORCH_DEFAULT_TIER,
            mode=MessageMode.append,
            deterministic=False,
            confidence=0.5,
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
        del messages, stream, run_id, task_id, agent_role, budget_scope, reasoning_effort, on_delta
        return LLMResponse(
            text="",
            call_id="",
            model="stub",
            provider="",
            tier=tier or ORCH_DEFAULT_TIER,
            finish_reason="stop",
            cost_usd=0.0,
            latency_ms=0,
            fallback_index=0,
            deterministic=True,
            fixture_key=None,
        )
