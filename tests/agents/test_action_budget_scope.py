"""Блокер ревью #350: действия не списываются в internal-бакет."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import BudgetScope, PlanStep, TaskStatus
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
