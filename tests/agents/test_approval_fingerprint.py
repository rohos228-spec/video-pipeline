"""Отпечаток approve переживает рестарт и не даёт повторно оплатить шаг."""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


class _Planner:
    def __init__(self, plan: list[PlanStep]) -> None:
        self.plan = list(plan)
        self.calls = 0

    async def make_plan(self, goal: str, context: str = "", **kwargs: Any) -> list[PlanStep]:
        del goal, context, kwargs
        self.calls += 1
        return list(self.plan)


@pytest.mark.asyncio
async def test_restart_grant_does_not_replan_or_rerun(token: str) -> None:
    del token
    mark = PlanStep(action="mark", mutating=True)
    note = PlanStep(action="note", risk="high", mutating=True)
    planner = _Planner([mark, note])
    actions = MockActions(risks={"mark": "none", "note": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=planner,
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("fp-restart", "цель", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["mark"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert planner.calls == 1

        fresh_planner = _Planner([mark, note])
        fresh = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=fresh_planner,
        )
        set_runtime(fresh)
        await fresh.supervisor.recover_after_restart()
        await fresh.supervisor.grant_approval("fp-restart")
        await fresh.supervisor.wait(task_id, timeout=5)
        assert fresh_planner.calls == 0
        assert [call["name"] for call in actions.calls] == ["mark", "note"]
        assert fresh.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_paid_outcome_grant_does_not_replan(token: str) -> None:
    del token
    step = PlanStep(
        action="charge",
        params={"_wait": True, "_cost": 1, "_change_id": "chg-1"},
        paid=True,
        mutating=True,
    )
    planner = _Planner([step])
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=planner,
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("fp-paid", "платный", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval("fp-paid")
        await actions.started.wait()
        runtime.supervisor.request_cancel("fp-paid")
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert planner.calls == 1
        assert [call["name"] for call in actions.calls] == ["charge"]
        await runtime.supervisor.grant_approval("fp-paid")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert planner.calls == 1
        assert [call["name"] for call in actions.calls] == ["charge"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_running_paid_row_blocks_same_action_with_other_params(token: str) -> None:
    del token
    running = PlanStep(action="charge", params={"n": 1}, paid=True, mutating=True)
    other = PlanStep(action="charge", params={"n": 2}, paid=True, mutating=True)
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=_Planner([other]),
    )
    set_runtime(runtime)
    try:
        task_id = runtime.tasks.insert("fp-open", "платный", TaskStatus.executing.value, project_id=13)
        runtime.tasks.insert_step(task_id, 0, running)
        runtime.checkpoints.save(
            task_id,
            "fp-open",
            CheckpointData(plan=[other], position=0),
        )
        fresh = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=_Planner([other]),
        )
        set_runtime(fresh)
        recovered = await fresh.supervisor.recover_after_restart()
        assert task_id in recovered
        await fresh.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert fresh.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approvals = [event for event in fresh.bus.replay("fp-open", 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "paid_unknown_outcome"
    finally:
        set_runtime(None)
