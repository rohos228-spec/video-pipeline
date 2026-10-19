"""approval_required несёт actions и, для paid_outcome_unknown, change_id шага."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, Risk, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

_PAID_ACTIONS = ["approve", "retry_step", "deny"]
_ORDINARY_ACTIONS = ["approve"]


@pytest.mark.asyncio
async def test_paid_unknown_approval_carries_step_change_id_and_actions(token: str) -> None:
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
                )
            ]
        ),
    )
    set_runtime(runtime)
    session = "appr-paid"
    try:
        task_id = await runtime.supervisor.start_task(session, "платный", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval(session)
        await actions.started.wait()
        runtime.supervisor.request_kill(session)
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        checkpoint = runtime.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        opened = [
            event
            for event in runtime.bus.replay(session, 0)
            if event.type == "approval_required" and event.payload.get("reason") == "paid_outcome_unknown"
        ]
        assert opened
        payload = opened[-1].payload
        assert payload["step_change_id"] in checkpoint.change_ids
        assert payload["step_change_id"] == "chg-keep"
        assert payload["actions"] == _PAID_ACTIONS
        assert str(payload["change_id"]).startswith("orch-appr-")
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_ordinary_approval_lists_actions(token: str) -> None:
    del token
    actions = MockActions(risks={"charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="charge", risk="low", mutating=True)]),
    )
    set_runtime(runtime)
    session = "appr-risk"
    try:
        task_id = await runtime.supervisor.start_task(session, "риск", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        opened = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        assert len(opened) == 1
        assert opened[0].payload["reason"] == "risk"
        assert opened[0].payload["risk"] == Risk.high.value
        assert opened[0].payload["actions"] == _ORDINARY_ACTIONS
        assert "step_change_id" not in opened[0].payload
        assert actions.calls == []
    finally:
        set_runtime(None)
