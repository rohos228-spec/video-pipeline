"""Kill во время платного шага не затирает change_id последнего чекпоинта."""

from __future__ import annotations

import asyncio

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_paid_kill_checkpoint_keeps_step_change_id(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(
                    action="charge",
                    params={"_wait": True, "_cost": 1, "_change_id": "chg-keep"},
                    paid=True,
                    mutating=True,
                    estimated_cost=1,
                ),
                PlanStep(action="save", mutating=True),
            ]
        ),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("paid-kill-cp", "платный", project_id=13)
        await actions.started.wait()
        notes = runtime.bus.subscribe("paid-kill-cp")
        runtime.supervisor.request_kill("paid-kill-cp")
        while True:
            event = await asyncio.wait_for(notes.get(), 5)
            if event.type == "approval_required" and event.payload.get("reason") == "paid_outcome_unknown":
                break
        runtime.bus.unsubscribe("paid-kill-cp", notes)
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        row = runtime.tasks.list_steps(task_id)[0]
        assert row["change_id"] == "chg-keep"
        assert row["status"] == "running"
        checkpoint = runtime.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        assert "chg-keep" in checkpoint.change_ids
        assert float(checkpoint.spend.actions) == 1.0
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 1.0
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert [call["name"] for call in actions.calls] == ["charge"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_overlapping_paid_outcome_mark_stays_waiting_approval(token: str) -> None:
    """Cancel и сразу release: второй mark не роняет задачу в failed."""
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(
                    action="charge",
                    params={"_wait": True, "_cost": 1, "_change_id": "chg-race"},
                    paid=True,
                    mutating=True,
                    estimated_cost=1,
                ),
            ]
        ),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("paid-race", "платный", project_id=13)
        await actions.started.wait()
        runtime.supervisor.request_cancel("paid-race")
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.tasks.get(task_id)["error"] is None
        assert [call["name"] for call in actions.calls] == ["charge"]
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 1.0
        assert runtime.tasks.list_steps(task_id)[0]["change_id"] == "chg-race"
    finally:
        set_runtime(None)
