"""Переходы FSM. Статус пишет только супервизор."""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import InvalidTransition
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter


@pytest.mark.asyncio
async def test_happy_path_statuses(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="noop", params={}, expected="ok", paid=False, mutating=False)]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("fsm", "проверь статус")
        await rt.supervisor.wait(task_id, timeout=5)
        assert [status for _, status in rt.supervisor.history] == [
            TaskStatus.queued.value,
            TaskStatus.planning.value,
            TaskStatus.critic_review.value,
            TaskStatus.executing.value,
            TaskStatus.done.value,
        ]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert actions.calls[0]["name"] == "noop"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_reject_goes_to_waiting_approval(token: str) -> None:
    del token
    rt = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        critic=ScriptedCritic(["reject"]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("fsm-rej", "цель")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert rt.supervisor.history[-1][1] == TaskStatus.waiting_approval.value
        assert any(event.type == "approval_required" for event in rt.bus.replay("fsm-rej", 0))
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_terminal_status_is_not_rewritten(token: str) -> None:
    del token
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter())
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("fsm-lock", "цель")
        await rt.supervisor.wait(task_id, timeout=5)
        with pytest.raises(InvalidTransition):
            await rt.supervisor._set_status(task_id, TaskStatus.planning, session_id="fsm-lock")
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_budget_exhausted_uses_waiting_approval(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="first", params={}, expected="ok", paid=False, mutating=False),
                PlanStep(action="second", params={}, expected="ok", paid=False, mutating=False),
            ]
        ),
    )

    async def _on_execute(name: str, params: dict[str, Any], ctx: dict[str, Any]) -> None:
        del params, ctx
        if name == "first":
            rt.supervisor.request_approval("fsm-budget", "budget_exhausted")

    actions.on_execute = _on_execute
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("fsm-budget", "цель")
        await rt.supervisor.wait(task_id, timeout=5)
        statuses = {item.value for item in TaskStatus}
        assert "escalated" not in statuses
        assert "budget_exhausted" not in statuses
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert rt.supervisor.history[-1][1] == TaskStatus.waiting_approval.value
        approvals = [
            event for event in rt.bus.replay("fsm-budget", 0) if event.type == "approval_required"
        ]
        assert approvals[-1].payload["reason"] == "budget_exhausted"
        assert [call["name"] for call in actions.calls] == ["first"]
    finally:
        set_runtime(None)
