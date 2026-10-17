"""Заглушки planner / critic / executor / роутера до R2–R3.

Реальных LLM и платных действий нет (D7, R0b ещё не в main).
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.contracts import (
    ActionResult,
    ClassifyResult,
    LLMResponse,
    MessageMode,
    PlanStep,
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
    """Мок-заглушка, пока WS2 не отдал ``actions.execute``."""

    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> ActionResult:
        del params, task_ctx
        return ActionResult(
            ok=False,
            data={"error": "actions.execute не подключён (ждёт WS2)", "action": name},
            change_id=None,
            cost=0.0,
        )


class UnlinkedValidators:
    """Мок-заглушка, пока WS1 не отдал ``validators.run``."""

    async def run(self, name: str, project_id: int, context: dict[str, Any]) -> ValidatorResult:
        del project_id, context
        return ValidatorResult(name=name, passed=True, details="validators.run не подключён (ждёт WS1)", blocking=False)


class FallbackRouter:
    """Роутер-заглушка до WS5. Сеть не вызывает.

    Спорная классификация уходит в append/fast, чтобы чат R1 мог стримить ответ.
    """

    async def classify(self, message: str) -> ClassifyResult:
        del message
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.append,
            deterministic=False,
            confidence=0.5,
        )

    async def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
    ) -> LLMResponse:
        del messages, stream
        return LLMResponse(
            text="",
            call_id="",
            model="stub",
            provider="",
            tier=tier or "fast",
            finish_reason="stop",
            cost_usd=0.0,
            latency_ms=0,
            fallback_index=0,
            deterministic=True,
            fixture_key=None,
        )
