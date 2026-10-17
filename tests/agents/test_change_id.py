"""Серверный change_id на approval_required ядра. Клиентский id не используется."""

from __future__ import annotations

import json
import re

import pytest

from app.orchestrator.agents.critic import PlanCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import ApprovalGrantError
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import (
    ApprovalDecision,
    ApprovalEvent,
    CheckpointData,
    LLMResponse,
    PlanStep,
    TaskStatus,
)
from app.web.routers.orchestrator_chat import MessageIn
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter

_CHANGE_ID = re.compile(r"^orch-appr-(\d+)-(\d+)$")


def _assert_server_id(change_id: object, task_id: int) -> str:
    assert isinstance(change_id, str) and change_id
    match = _CHANGE_ID.fullmatch(change_id)
    assert match is not None
    assert int(match.group(1)) == task_id
    assert change_id != "client-forged"
    assert change_id != "orch-appr-5-99"
    return change_id


def _approvals(runtime, session: str) -> list:
    return [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]


@pytest.mark.asyncio
async def test_change_id_server_generated_only(token: str) -> None:
    del token
    body = MessageIn.model_validate({"text": "сделай", "change_id": "orch-appr-1-99"})
    assert "change_id" not in body.model_dump()

    session = "appr-server-id"
    actions = MockActions(risks={"charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(
                    action="charge",
                    risk="low",
                    mutating=True,
                    expected="pay",
                    params={"change_id": "client-forged"},
                )
            ]
        ),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, "списать", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        opened = _approvals(runtime, session)
        assert len(opened) == 1
        change_id = _assert_server_id(opened[0].payload["change_id"], task_id)
        assert change_id == f"orch-appr-{task_id}-1"
        assert opened[0].payload["reason"] == "risk"
        assert actions.calls == []

        await runtime.supervisor.publish_approval_required(
            session,
            {"reason": "risk", "task_id": task_id, "change_id": "orch-appr-5-99"},
            task_id=task_id,
        )
        again = _approvals(runtime, session)
        assert _assert_server_id(again[-1].payload["change_id"], task_id) == f"orch-appr-{task_id}-2"
        assert all(event.payload["change_id"] != "orch-appr-5-99" for event in again)
        assert all(event.payload["change_id"] != "client-forged" for event in again)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_core_approval_events_always_have_change_id(token: str) -> None:
    del token
    await _risk_ids_grow()
    await _reason_id("critic", _critic_runtime, project_id=13)
    await _reason_id("missing", _missing_runtime, project_id=None)
    await _budget_id()
    await _paid_id()


async def _risk_ids_grow() -> None:
    session = "appr-risk-seq"
    actions = MockActions(risks={"charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="charge", risk="low", mutating=True, expected="one"),
                PlanStep(action="charge", risk="low", mutating=True, expected="two"),
            ]
        ),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, "два риска", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        first = _assert_server_id(_approvals(runtime, session)[-1].payload["change_id"], task_id)
        assert first == f"orch-appr-{task_id}-1"
        assert _approvals(runtime, session)[-1].payload["reason"] == "risk"
        await runtime.supervisor.grant_approval(session, change_id=first)
        await runtime.supervisor.wait(task_id, timeout=5)
        second = _assert_server_id(_approvals(runtime, session)[-1].payload["change_id"], task_id)
        assert second == f"orch-appr-{task_id}-2"
        assert [call["name"] for call in actions.calls] == ["charge"]
        decided = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_decided"]
        assert decided[-1].payload["change_id"] == first
        assert decided[-1].payload["decision"] is None
    finally:
        set_runtime(None)


def _critic_runtime():
    return build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look")]),
        critic=ScriptedCritic(["revise", "revise", "revise"]),
    )


def _missing_runtime():
    return build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="edit", mutating=True)]),
    )


async def _reason_id(name: str, factory, project_id: int | None) -> None:
    session = f"appr-{name}"
    runtime = factory()
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, name, project_id=project_id)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        opened = _approvals(runtime, session)
        _assert_server_id(opened[-1].payload["change_id"], task_id)
        assert opened[-1].payload["change_id"] == f"orch-appr-{task_id}-1"
    finally:
        set_runtime(None)


async def _budget_id() -> None:
    session = "appr-budget"
    text = json.dumps({"steps": [{"action": "noop", "expected": "ok", "mutating": True}]})
    router = ScriptedRouter()
    router.responses = [
        LLMResponse(
            text=text,
            call_id="budget-call",
            model="scripted",
            provider="52pay",
            tier="strong",
            finish_reason="stop",
            task_status=TaskStatus.waiting_approval,
            approval=ApprovalEvent(
                reason="budget_exhausted",
                change_id="client-forged",
                payload={"spent_usd": 10, "budget_usd": 10, "run_id": "run"},
            ),
        )
    ]
    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router),
        critic=PlanCritic(router),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, "план")
        await runtime.supervisor.wait(task_id, timeout=5)
        opened = _approvals(runtime, session)
        assert opened[-1].payload["reason"] == "budget_exhausted"
        assert _assert_server_id(opened[-1].payload["change_id"], task_id) == f"orch-appr-{task_id}-1"
    finally:
        set_runtime(None)


async def _paid_id() -> None:
    session = "appr-paid"
    step = PlanStep(action="charge", params={"_cost": 1.5}, expected="paid", paid=True, mutating=True)
    tasks = TaskStore()
    task_id = tasks.insert(session, "списать", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, [step], position=0)
    tasks.insert_step(task_id, 0, step)
    CheckpointStore().save(task_id, session, CheckpointData(plan=[step], position=0))
    runtime = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        opened = _approvals(runtime, session)
        assert opened[-1].payload["reason"] == "paid_outcome_unknown"
        assert _assert_server_id(opened[-1].payload["change_id"], task_id) == f"orch-appr-{task_id}-1"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_change_id_survives_restart(token: str) -> None:
    del token
    session = "appr-restart"
    actions = MockActions(risks={"charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="charge", risk="low", mutating=True, expected="pay")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, "списать", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        change_id = _assert_server_id(_approvals(runtime, session)[-1].payload["change_id"], task_id)
        stored = runtime.checkpoints.load_latest(task_id)
        assert stored is not None
        assert change_id in stored.context
    finally:
        set_runtime(None)

    restarted = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="charge", risk="low", mutating=True, expected="pay")]),
    )
    set_runtime(restarted)
    try:
        await restarted.supervisor.recover_after_restart()
        assert restarted.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert _approvals(restarted, session)[-1].payload["change_id"] == change_id
        assert restarted.supervisor._appr_open.get(task_id) == change_id
        latest = restarted.checkpoints.load_latest(task_id)
        assert latest is not None and change_id in latest.context
        await restarted.supervisor.grant_approval(session, change_id=change_id)
        await restarted.supervisor.wait(task_id, timeout=5)
        decided = [event for event in restarted.bus.replay(session, 0) if event.type == "approval_decided"]
        assert decided[0].payload["change_id"] == change_id
        assert decided[0].payload["decision"] is None
        assert decided[0].payload["task_id"] == task_id
        assert change_id in restarted.supervisor._appr_decided
    finally:
        set_runtime(None)


def _open_paid(session: str) -> int:
    paid = PlanStep(
        action="charge",
        params={"_cost": 1.5},
        expected="paid",
        paid=True,
        mutating=True,
    )
    nxt = PlanStep(action="next", expected="second", paid=False, mutating=True)
    tasks = TaskStore()
    task_id = tasks.insert(session, "списать", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, [paid, nxt], position=0)
    tasks.insert_step(task_id, 0, paid)
    CheckpointStore().save(task_id, session, CheckpointData(plan=[paid, nxt], position=0))
    return task_id


def _snapshot(runtime, session: str, task_id: int) -> dict:
    steps = [(row["action_name"], row["status"]) for row in runtime.tasks.list_steps(task_id)]
    return {
        "calls": [call["name"] for call in runtime.supervisor.actions.calls],
        "status": runtime.tasks.get(task_id)["status"],
        "events": len(runtime.bus.replay(session, 0)),
        "armed": runtime.supervisor._retry_armed.get(task_id),
        "steps": steps,
    }


async def _wait_paid(session: str) -> tuple:
    task_id = _open_paid(session)
    actions = MockActions()
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    await runtime.supervisor.recover_after_restart()
    await runtime.supervisor.wait(task_id, timeout=5)
    change_id = _approvals(runtime, session)[-1].payload["change_id"]
    return runtime, task_id, change_id


@pytest.mark.asyncio
async def test_approve_resolved_change_id_409(token: str) -> None:
    del token
    session = "appr-resolved"
    runtime, task_id, first_id = await _wait_paid(session)
    try:
        await runtime.supervisor.publish_approval_required(
            session,
            {"reason": "paid_outcome_unknown", "task_id": task_id, "change_id": "orch-appr-5-99"},
            task_id=task_id,
        )
        current = _approvals(runtime, session)[-1].payload["change_id"]
        assert current == f"orch-appr-{task_id}-2"
        assert first_id == f"orch-appr-{task_id}-1"
        before = _snapshot(runtime, session, task_id)
        with pytest.raises(ApprovalGrantError) as stale:
            await runtime.supervisor.grant_approval(session, ApprovalDecision.approve, change_id=first_id)
        assert stale.value.status_code == 409
        assert _snapshot(runtime, session, task_id) == before

        await runtime.supervisor.grant_approval(session, ApprovalDecision.approve, change_id=current)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in runtime.supervisor.actions.calls] == ["next"]
        settled = _snapshot(runtime, session, task_id)
        with pytest.raises(ApprovalGrantError) as again:
            await runtime.supervisor.grant_approval(session, ApprovalDecision.approve, change_id=current)
        assert again.value.status_code == 409
        assert _snapshot(runtime, session, task_id) == settled
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [ApprovalDecision.approve, ApprovalDecision.deny, ApprovalDecision.retry_step])
async def test_repeat_decision_same_change_id_409(token: str, decision: ApprovalDecision) -> None:
    del token
    session = f"appr-repeat-{decision.value}"
    runtime, task_id, change_id = await _wait_paid(session)
    try:
        await runtime.supervisor.grant_approval(session, decision, change_id=change_id)
        if decision is ApprovalDecision.approve:
            await runtime.supervisor.wait(task_id, timeout=5)
        settled = _snapshot(runtime, session, task_id)
        with pytest.raises(ApprovalGrantError) as exc:
            await runtime.supervisor.grant_approval(session, decision, change_id=change_id)
        assert exc.value.status_code == 409
        assert _snapshot(runtime, session, task_id) == settled
        if decision is ApprovalDecision.approve:
            assert settled["calls"] == ["next"]
        else:
            assert settled["calls"] == []
        if decision is ApprovalDecision.retry_step:
            assert settled["status"] == TaskStatus.waiting_approval.value
            assert settled["armed"]
    finally:
        set_runtime(None)
