"""Отпечаток approve переживает рестарт и не даёт повторно оплатить шаг."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ApprovalDecision, CheckpointData, PlanStep, TaskStatus
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
    follow = PlanStep(action="note", mutating=True)
    planner = _Planner([step, follow])
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
        await runtime.supervisor.grant_approval("fp-paid", ApprovalDecision.approve)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert planner.calls == 1
        assert [call["name"] for call in actions.calls] == ["charge", "note"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        row = runtime.tasks.list_steps(task_id)[0]
        assert row["status"] == "done"
        assert row["change_id"] == "chg-1"
        assert task_id not in runtime.supervisor._paid_outcome_hold
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_paid_outcome_deny_cancels_and_drains_queue(token: str) -> None:
    del token
    charge = PlanStep(
        action="charge",
        params={"_wait": True, "_cost": 1, "_change_id": "chg-1"},
        paid=True,
        mutating=True,
    )
    note = PlanStep(action="note", mutating=True)
    planner = _Planner([charge])
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=planner,
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("fp-deny", "платный", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval("fp-deny")
        await actions.started.wait()
        runtime.supervisor.request_cancel("fp-deny")
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        held = runtime.tasks.list_steps(task_id)[0]
        assert held["change_id"] == "chg-1"
        planner.plan = [note]
        queued = await runtime.supervisor.submit_task("fp-deny", "следующая", project_id=13)
        assert runtime.tasks.get(queued)["status"] == TaskStatus.queued.value
        runtime.supervisor.request_cancel("fp-deny")
        pending = [job for job in runtime.supervisor._shield_jobs if not job.done()]
        if pending:
            await asyncio.wait(pending, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
        kept = runtime.tasks.list_steps(task_id)[0]
        assert kept["change_id"] == "chg-1"
        await runtime.supervisor.wait(queued, timeout=5)
        assert runtime.tasks.get(queued)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["charge", "note"]
    finally:
        actions.release.set()
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
        assert approvals[-1].payload["reason"] == "paid_outcome_unknown"
    finally:
        set_runtime(None)
