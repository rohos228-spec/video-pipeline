"""Рестарт процесса: queued, чекпоинт, неизвестный платный шаг."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import (
    CONTRACT_VERSION,
    VALIDATOR_CONTEXT_DRY_RUN,
    ApprovalDecision,
    CheckpointData,
    PlanStep,
    TaskStatus,
    approval_decision_choices,
    export_contract_schemas,
    forecast_cost,
    paid_step_identity,
)
from tests.agents.mocks import MockActions, MockValidators, ScriptedCritic, ScriptedRouter


def _runtime(actions: MockActions):
    return build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
    )


@pytest.mark.asyncio
async def test_restart_mid_task_resumes_from_checkpoint(token: str) -> None:
    del token
    session = "restart-mid"
    steps = [
        PlanStep(
            action="keep",
            params={"_change_id": "chg-keep"},
            expected="first",
            paid=False,
            mutating=True,
        ),
        PlanStep(
            action="next",
            params={"_change_id": "chg-next"},
            expected="second",
            paid=False,
            mutating=True,
        ),
    ]
    tasks = TaskStore()
    task_id = tasks.insert(session, "доделать", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, steps, position=1)
    done_id = tasks.insert_step(task_id, 0, steps[0])
    tasks.finish_step(done_id, "done", "chg-keep", 0.0)
    CheckpointStore().save(
        task_id,
        session,
        CheckpointData(plan=steps, position=1, change_ids=["chg-keep"]),
    )
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        started = await runtime.supervisor.recover_after_restart()
        assert started == [task_id]
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["next"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        latest = runtime.checkpoints.load_latest(task_id)
        assert latest is not None
        assert latest.position == 2
        assert latest.change_ids == ["chg-keep", "chg-next"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_restart_requeues_queued_tasks(token: str) -> None:
    del token
    session = "restart-queue"
    tasks = TaskStore()
    first = tasks.insert(session, "первая", TaskStatus.queued.value)
    second = tasks.insert(session, "вторая", TaskStatus.queued.value)
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        assert runtime.supervisor._queued == {}
        started = await runtime.supervisor.recover_after_restart()
        assert started == [first]
        assert [task_id for task_id, _goal in runtime.supervisor._queued[session]] == [second]
        await runtime.supervisor.wait(first, timeout=5)
        await runtime.supervisor.wait(second, timeout=5)
        assert runtime.tasks.get(first)["status"] == TaskStatus.done.value
        assert runtime.tasks.get(second)["status"] == TaskStatus.done.value
        assert [call["params"]["goal"] for call in actions.calls] == ["первая", "вторая"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_restart_does_not_rerun_paid_step(token: str) -> None:
    del token
    session = "restart-paid"
    step = PlanStep(
        action="charge",
        params={"_change_id": "chg-paid", "_cost": 1.5},
        expected="paid",
        paid=True,
        mutating=True,
    )
    tasks = TaskStore()
    task_id = tasks.insert(session, "списать", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, [step], position=0)
    tasks.insert_step(task_id, 0, step)
    CheckpointStore().save(
        task_id,
        session,
        CheckpointData(plan=[step], position=0),
    )
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approvals = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "paid_outcome_unknown"
        assert runtime.tasks.list_steps(task_id)[0]["status"] == "running"

        await runtime.supervisor.grant_approval(session)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


def _paid_unknown(session: str, steps: list[PlanStep]) -> int:
    tasks = TaskStore()
    task_id = tasks.insert(session, "списать", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, steps, position=0)
    tasks.insert_step(task_id, 0, steps[0])
    CheckpointStore().save(task_id, session, CheckpointData(plan=steps, position=0))
    return task_id


@pytest.mark.asyncio
async def test_paid_outcome_unknown_approve_marks_done_and_continues(token: str) -> None:
    del token
    session = "paid-approve"
    paid = PlanStep(
        action="charge",
        params={"_change_id": "chg-paid", "_cost": 1.5},
        expected="paid",
        paid=True,
        mutating=True,
    )
    nxt = PlanStep(
        action="next",
        params={"_change_id": "chg-next"},
        expected="second",
        paid=False,
        mutating=True,
    )
    task_id = _paid_unknown(session, [paid, nxt])
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        schema = export_contract_schemas()
        assert schema["version"] == CONTRACT_VERSION == "ws3-r3.3"
        body = schema["models"]["ApprovalDecisionBody"]
        assert "decision" in body["properties"]
        assert "decision" not in body.get("required", [])
        assert schema["enums"]["ApprovalDecision"] == ["approve", "retry_step", "deny"]
        labels = {item["decision"]: item["label"] for item in approval_decision_choices()}
        assert labels == {
            "approve": "Шаг выполнен, продолжить",
            "retry_step": "Перезапустить шаг",
            "deny": "Остановить задачу",
        }
        assert schema["rules"]["approval_decision_labels"] == approval_decision_choices()

        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        opened = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        assert opened[-1].payload["decisions"] == approval_decision_choices()
        await runtime.supervisor.grant_approval(session, ApprovalDecision.approve)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["next"]
        rows = runtime.tasks.list_steps(task_id)
        assert rows[0]["status"] == "done"
        assert rows[0]["action_name"] == "charge"
        assert paid_step_identity(paid.action, paid.params) in runtime.supervisor._paid_done[task_id]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_paid_outcome_unknown_retry_requires_forecast_and_own_grant(token: str) -> None:
    del token
    session = "paid-retry"
    paid = PlanStep(
        action="charge",
        params={"_cost": 1.5},
        expected="paid",
        paid=True,
        mutating=True,
        estimated_cost=4.25,
    )
    task_id = _paid_unknown(session, [paid])
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        opened = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        assert opened[-1].payload["forecast_usd"] == forecast_cost([paid]) == 4.25
        forecast = await runtime.supervisor.grant_approval(session, "retry_step")
        assert forecast is not None
        assert forecast["executed"] is False
        assert forecast["forecast_usd"] == 4.25
        assert actions.calls == []
        await runtime.supervisor.grant_approval(session)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        started = await runtime.supervisor.grant_approval(session, retry_grant=True)
        assert started is not None and started["executed"] is True
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["charge"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_paid_outcome_unknown_retry_grant_is_single_use(token: str) -> None:
    del token
    session = "paid-retry-once"
    paid = PlanStep(
        action="charge",
        params={"_cost": 2},
        expected="paid",
        paid=True,
        mutating=True,
        estimated_cost=3,
    )
    task_id = _paid_unknown(session, [paid])
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval(session, ApprovalDecision.retry_step)
        runtime.supervisor._budget_stop.add(task_id)
        blocked = await runtime.supervisor.grant_approval(session, retry_grant=True)
        assert blocked is not None
        assert blocked["executed"] is False
        assert blocked["reason"] == "budget_exhausted"
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        runtime.supervisor._budget_stop.discard(task_id)
        started = await runtime.supervisor.grant_approval(session, retry_grant=True)
        assert started is not None and started["executed"] is True
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["charge"]
        runtime.tasks.set_status(task_id, TaskStatus.waiting_approval.value)
        again = await runtime.supervisor.grant_approval(session, retry_grant=True)
        assert again is not None
        assert again["executed"] is False
        assert again["reason"] == "retry_grant_spent"
        assert [call["name"] for call in actions.calls] == ["charge"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_paid_outcome_unknown_deny_fails_task(token: str) -> None:
    del token
    session = "paid-deny"
    paid = PlanStep(
        action="charge",
        params={"_cost": 1.5},
        expected="paid",
        paid=True,
        mutating=True,
    )
    task_id = _paid_unknown(session, [paid])
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval(session, ApprovalDecision.deny)
        assert actions.calls == []
        assert runtime.tasks.list_steps(task_id)[0]["status"] == "failed"
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "paid_outcome_unknown"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_restart_verify_then_run_node_approves_the_paid_step(token: str) -> None:
    """Чекпоинт на verify_code_state не подменяет платный run_node."""
    del token
    session = "restart-verify-node"
    verify = PlanStep(action="verify_code_state", expected="ok", paid=False, mutating=False)
    paid = PlanStep(
        action="run_node",
        params={"node_key": "n_qc"},
        expected="ran",
        paid=True,
        mutating=True,
        estimated_cost=6.5,
    )
    tasks = TaskStore()
    task_id = tasks.insert(session, "нода", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, [verify, paid], position=0)
    tasks.insert_step(task_id, 1, paid)
    CheckpointStore().save(task_id, session, CheckpointData(plan=[verify, paid], position=0))
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["verify_code_state"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        pinned = runtime.checkpoints.load_latest(task_id)
        assert pinned is not None
        assert pinned.position == 1
        assert pinned.plan[pinned.position].action == "run_node"
        opened = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        assert opened[-1].payload["action"] == "run_node"
        assert opened[-1].payload["position"] == 1
        assert opened[-1].payload["forecast_usd"] == forecast_cost([paid]) == 6.5
        node = next(row for row in runtime.tasks.list_steps(task_id) if row["action_name"] == "run_node")
        assert node["status"] == "running"
        await runtime.supervisor.grant_approval(session, ApprovalDecision.approve)
        await runtime.supervisor.wait(task_id, timeout=5)
        node = next(row for row in runtime.tasks.list_steps(task_id) if row["action_name"] == "run_node")
        assert node["status"] == "done"
        assert paid_step_identity(paid.action, paid.params) in runtime.supervisor._paid_done[task_id]
        assert [call["name"] for call in actions.calls] == ["verify_code_state"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_approve_unknown_outcome_counts_estimated_spend(token: str) -> None:
    del token
    session = "paid-estimate"
    paid = PlanStep(
        action="charge",
        params={"_change_id": "chg-paid"},
        expected="paid",
        paid=True,
        mutating=True,
        estimated_cost=4.25,
    )
    nxt = PlanStep(action="next", expected="second", paid=False, mutating=True)
    task_id = _paid_unknown(session, [paid, nxt])
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        before = float(runtime.tasks.get(task_id)["spend_actions"] or 0)
        await runtime.supervisor.grant_approval(session, ApprovalDecision.approve)
        await runtime.supervisor.wait(task_id, timeout=5)
        row = runtime.tasks.list_steps(task_id)[0]
        assert row["status"] == "done"
        assert row["cost"] == pytest.approx(4.25)
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == pytest.approx(before + 4.25)
        latest = runtime.checkpoints.load_latest(task_id)
        assert latest is not None
        assert latest.spend.actions == pytest.approx(4.25)
        assert "estimate charge 4.25" in latest.context
        assert [call["name"] for call in actions.calls] == ["next"]
    finally:
        set_runtime(None)


def _draft(session: str, status: TaskStatus) -> int:
    step = PlanStep(action="look", expected="ok", paid=False, mutating=True)
    tasks = TaskStore()
    task_id = tasks.insert(session, "черновик", status.value, project_id=13)
    tasks.save_plan(task_id, [step], position=0)
    CheckpointStore().save(task_id, session, CheckpointData(plan=[step], position=0))
    return task_id


@pytest.mark.asyncio
async def test_restart_planning_does_not_execute_until_critic_approves(token: str) -> None:
    """planning и critic_review после рестарта ждут approve. Dry-run идёт до execute."""
    del token
    rejected = _draft("restart-plan-reject", TaskStatus.planning)
    actions = MockActions()
    validators = MockValidators()
    runtime = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        critic=ScriptedCritic(["reject"]),
    )
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(rejected, timeout=5)
        assert actions.calls == []
        assert any(
            call["name"] == "plan_sanity" and call["context"].get(VALIDATOR_CONTEXT_DRY_RUN) is True
            for call in validators.calls
        )
        assert runtime.tasks.get(rejected)["status"] == TaskStatus.waiting_approval.value
        held = [event for event in runtime.bus.replay("restart-plan-reject", 0) if event.type == "approval_required"]
        assert held[-1].payload["reason"] == "critic_reject"
    finally:
        set_runtime(None)

    approved = _draft("restart-plan-approve", TaskStatus.critic_review)
    actions = MockActions()
    validators = MockValidators()
    runtime = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        critic=ScriptedCritic(["approve"]),
    )
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(approved, timeout=5)
        assert [call["name"] for call in actions.calls] == ["look"]
        assert any(
            call["name"] == "plan_sanity" and call["context"].get(VALIDATOR_CONTEXT_DRY_RUN) is True
            for call in validators.calls
        )
        assert runtime.tasks.get(approved)["status"] == TaskStatus.done.value
        assert runtime.tasks.get(rejected)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)
