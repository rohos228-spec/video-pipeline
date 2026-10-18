"""paid_outcome_unknown в батче из двух шагов относится к платному шагу."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus, forecast_cost, paid_step_identity
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


def _charge() -> PlanStep:
    return PlanStep(
        action="charge",
        params={"_cost": 1.5},
        expected="paid",
        paid=True,
        mutating=True,
        estimated_cost=4.25,
        parallel_group="batch",
    )


@pytest.mark.asyncio
async def test_two_step_batch_unknown_uses_the_paid_step(token: str) -> None:
    del token
    session = "batch-unknown"
    look = PlanStep(action="look", expected="read", parallel_group="batch")
    charge = _charge()
    tasks = TaskStore()
    task_id = tasks.insert(session, "списать", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, [look, charge], position=0)
    tasks.insert_step(task_id, 1, charge)
    CheckpointStore().save(task_id, session, CheckpointData(plan=[look, charge], position=0))
    actions = MockActions()
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        current = runtime.checkpoints.load_latest(task_id)
        assert current is not None
        assert [step.action for step in current.plan] == ["look", "charge"]
        assert int(current.position) == 1
        approvals = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        change_id = str(approvals[-1].payload["change_id"])
        forecast = await runtime.supervisor.grant_approval(session, "retry_step", change_id=change_id)
        assert forecast is not None
        assert forecast["forecast_usd"] == forecast_cost([charge])
        refreshed = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        assert refreshed[-1].payload["action"] == "charge"
        assert refreshed[-1].payload["position"] == 1
        approved = await runtime.supervisor.grant_approval(session, "approve", change_id=change_id)
        assert approved is not None
        assert approved["decision"] == "approve"
        assert actions.calls == []
        rows = runtime.tasks.list_steps(task_id)
        assert rows[0]["action_name"] == "charge"
        assert rows[0]["status"] == "done"
        assert paid_step_identity(charge.action, charge.params) in runtime.supervisor._paid_done[task_id]
        assert paid_step_identity(look.action, look.params) not in runtime.supervisor._paid_done[task_id]
    finally:
        set_runtime(None)
