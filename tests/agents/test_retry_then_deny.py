"""После настоящего retry_step approve/deny живы, пока ключ не потрачен."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import ApprovalDecision, CheckpointData, PlanStep, TaskStatus
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


def _paid(session: str) -> int:
    step = PlanStep(
        action="charge",
        params={"_cost": 1.5},
        expected="paid",
        paid=True,
        mutating=True,
        estimated_cost=4.25,
    )
    tasks = TaskStore()
    task_id = tasks.insert(session, "списать", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, [step], position=0)
    tasks.insert_step(task_id, 0, step)
    CheckpointStore().save(task_id, session, CheckpointData(plan=[step], position=0))
    return task_id


def _runtime(actions: MockActions):
    return build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
    )


def _change_id(runtime, session: str) -> str:
    approvals = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
    return str(approvals[-1].payload["change_id"])


async def _arm_retry(session: str, actions: MockActions) -> tuple:
    runtime = _runtime(actions)
    set_runtime(runtime)
    task_id = _paid(session)
    await runtime.supervisor.recover_after_restart()
    await runtime.supervisor.wait(task_id, timeout=5)
    change_id = _change_id(runtime, session)
    armed = await runtime.supervisor.grant_approval(session, "retry_step", change_id=change_id)
    assert armed is not None
    assert armed["retry_armed"] is True
    assert actions.calls == []
    return runtime, task_id, change_id


@pytest.mark.asyncio
async def test_retry_step_then_deny(token: str) -> None:
    del token
    actions = MockActions()
    session = "retry-then-deny"
    runtime, task_id, change_id = await _arm_retry(session, actions)
    try:
        denied = await runtime.supervisor.grant_approval(session, ApprovalDecision.deny, change_id=change_id)
        assert denied is not None
        assert denied["decision"] == "deny"
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        row = runtime.tasks.list_steps(task_id)[0]
        assert row["action_name"] == "charge"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_retry_step_then_budget_stop_then_deny(token: str) -> None:
    del token
    actions = MockActions()
    session = "retry-budget-deny"
    runtime, task_id, change_id = await _arm_retry(session, actions)
    try:
        runtime.supervisor._budget_stop.add(task_id)
        denied = await runtime.supervisor.grant_approval(session, ApprovalDecision.deny, change_id=change_id)
        assert denied is not None
        assert denied["decision"] == "deny"
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.list_steps(task_id)[0]["action_name"] == "charge"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_retry_step_survives_restart_for_grant_and_deny(token: str) -> None:
    del token
    actions = MockActions()
    grant_session = "retry-restart-grant"
    deny_session = "retry-restart-deny"
    _runtime_grant, grant_id, grant_change = await _arm_retry(grant_session, actions)
    _runtime_deny, deny_id, deny_change = await _arm_retry(deny_session, actions)
    fresh = _runtime(actions)
    set_runtime(fresh)
    try:
        await fresh.supervisor.recover_after_restart()
        started = await fresh.supervisor.grant_approval(
            grant_session,
            retry_grant=True,
            change_id=grant_change,
        )
        assert started is not None
        assert started.get("reason") != "retry_grant_required"
        assert started["executed"] is True
        await fresh.supervisor.wait(grant_id, timeout=5)
        denied = await fresh.supervisor.grant_approval(
            deny_session,
            ApprovalDecision.deny,
            change_id=deny_change,
        )
        assert denied is not None
        assert denied["decision"] == "deny"
        assert fresh.tasks.get(deny_id)["status"] == TaskStatus.failed.value
        assert [call["name"] for call in actions.calls] == ["charge"]
    finally:
        set_runtime(None)
