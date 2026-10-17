"""Блокер ревью #350: рестарт из executing не повторяет платный шаг."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_restart_mid_paid_step_waits_instead_of_rerunning(token: str) -> None:
    del token
    actions = MockActions()
    step = PlanStep(action="charge", params={"n": 1}, paid=True, mutating=True)
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(runtime)
    try:
        task_id = runtime.tasks.insert("restart-paid", "платный", TaskStatus.executing.value, project_id=13)
        runtime.tasks.insert_step(task_id, 0, step)
        runtime.checkpoints.save(
            task_id,
            "restart-paid",
            CheckpointData(plan=[step], position=0),
        )
        fresh = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner([step]),
        )
        set_runtime(fresh)
        recovered = await fresh.supervisor.recover_after_restart()
        assert task_id in recovered
        await fresh.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert fresh.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approvals = [event for event in fresh.bus.replay("restart-paid", 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "paid_outcome_unknown"
        row = fresh.tasks.list_steps(task_id)[0]
        assert row["status"] == "running"
    finally:
        set_runtime(None)
