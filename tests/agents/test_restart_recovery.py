"""Рестарт процесса: queued, чекпоинт, неизвестный платный шаг."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


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
