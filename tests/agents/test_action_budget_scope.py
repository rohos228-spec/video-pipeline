"""Блокер ревью #350: действия не списываются в internal-бакет.

Набор scope тот же, что у WS5: только ``internal`` и ``pipeline``.
Неизвестный scope — ошибка до вызова. Пропуск scope остаётся internal.
"""

from __future__ import annotations

import pytest

from app.orchestrator.agents.llm_call import router_call
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import BudgetScope, PlanStep, TaskStatus, require_internal_run_id
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_action_context_uses_pipeline_budget_without_reasoning(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="charge", mutating=True)]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("scope-pipe", "шаг", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert actions.calls
        ctx = actions.calls[0]["ctx"]
        assert ctx["budget_scope"] == BudgetScope.pipeline.value
        assert ctx["run_id"] == f"orch-task-{task_id}"
        assert "reasoning_effort" not in ctx
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_unknown_budget_scope_is_rejected_before_the_call() -> None:
    router = ScriptedRouter()
    with pytest.raises(ValueError, match=r"internal\|pipeline"):
        await router.call(
            "strong",
            [{"role": "user", "content": "x"}],
            run_id="orch-task-1",
            budget_scope="external",
        )
    assert router.call_count == 0
    with pytest.raises(ValueError, match=r"internal\|pipeline"):
        require_internal_run_id(None, "sideways")
    with pytest.raises(ValueError, match="run_id"):
        require_internal_run_id(None, None)
    with pytest.raises(ValueError, match=r"internal\|pipeline"):
        await router_call(
            router,
            "strong",
            [{"role": "user", "content": "x"}],
            run_id="orch-task-1",
            budget_scope="external",
        )
    assert router.call_count == 0
    await router_call(
        router,
        "strong",
        [{"role": "user", "content": "x"}],
        run_id="orch-task-1",
    )
    assert router.call_count == 1
    assert router.last_call["budget_scope"] == BudgetScope.internal.value
