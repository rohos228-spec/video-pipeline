"""Заглушки planner / critic / executor / роутера до R2–R3.

Реальных LLM и платных действий нет (D7, R0b ещё не в main).
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.agents.llm_call import router_call
from app.orchestrator.contracts import (
    ORCH_DEFAULT_TIER,
    ActionResult,
    AgentRole,
    BudgetScope,
    ClassifyResult,
    LLMResponse,
    MessageMode,
    PlanStep,
    Risk,
    ValidatorResult,
)


class StubPlanner:
    async def make_plan(self, goal: str, context: str = "", **_call: Any) -> list[PlanStep]:
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
    async def review(self, plan: list[PlanStep], context: str = "", **_call: Any) -> str:
        del plan, context
        return "approve"


class ActionBackedExecutor:
    """Шаг плана → ``actions.execute``. До WS2 подставляется ``UnlinkedActions``.

    Если передан ``router``, перед действием идёт internal-вызов с kwargs
    из контекста шага (run_id, task_id, agent_role, budget_scope).
    """

    def __init__(self, actions: Any, router: Any | None = None) -> None:
        self.actions = actions
        self.router = router
        self.last_response: Any = None

    async def run(self, step: PlanStep, ctx: dict[str, Any]) -> ActionResult:
        self.last_response = None
        if self.router is not None:
            self.last_response = await router_call(
                self.router,
                "fast",
                [{"role": "user", "content": step.expected or step.action}],
                stream=False,
                run_id=ctx.get("run_id"),
                task_id=ctx.get("task_id"),
                agent_role=ctx.get("agent_role") or AgentRole.executor.value,
                budget_scope=ctx.get("budget_scope") or BudgetScope.internal.value,
            )
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
