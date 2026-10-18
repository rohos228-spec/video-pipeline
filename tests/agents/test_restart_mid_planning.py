"""Рестарт посреди планирования поднимает задачу и отпускает очередь."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _runtime(actions: MockActions):
    return build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look")]),
    )


@pytest.mark.asyncio
async def test_restart_mid_planning_restarts_drive_and_drains_queue(token: str) -> None:
    del token
    session = "mid-plan"
    tasks = TaskStore()
    planning_id = tasks.insert(session, "составь план", TaskStatus.planning.value, project_id=13)
    queued_id = tasks.insert(session, "следующая", TaskStatus.queued.value, project_id=13)
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        started = await runtime.supervisor.recover_after_restart()
        assert planning_id in started
        await runtime.supervisor.wait(planning_id, timeout=5)
        assert runtime.tasks.get(planning_id)["status"] == TaskStatus.done.value
        await runtime.supervisor.wait(queued_id, timeout=5)
        assert runtime.tasks.get(queued_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["look", "look"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_restart_executing_without_plan_is_empty_plan(token: str) -> None:
    del token
    session = "mid-exec"
    tasks = TaskStore()
    task_id = tasks.insert(session, "пустой", TaskStatus.executing.value, project_id=13)
    queued_id = tasks.insert(session, "после", TaskStatus.queued.value, project_id=13)
    actions = MockActions()
    runtime = _runtime(actions)
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "empty_plan"
        await runtime.supervisor.wait(queued_id, timeout=5)
        assert runtime.tasks.get(queued_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["look"]
    finally:
        set_runtime(None)
