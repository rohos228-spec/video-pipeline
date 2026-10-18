"""Память _unknown_step не перебивает шаг, который ещё running."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_stale_unknown_memory_does_not_move_the_checkpoint(token: str) -> None:
    del token
    first = PlanStep(action="charge", params={"n": 1}, paid=True, mutating=True, estimated_cost=1)
    second = PlanStep(action="charge", params={"n": 2}, paid=True, mutating=True, estimated_cost=1)
    session = "stale-unknown"
    tasks = TaskStore()
    task_id = tasks.insert(session, "план", TaskStatus.waiting_approval.value, project_id=13)
    tasks.insert_step(task_id, 1, second)
    CheckpointStore().save(
        task_id,
        session,
        CheckpointData(plan=[first, second], position=1),
    )
    runtime = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        runtime.supervisor._unknown_step[task_id] = {
            "position": 0,
            "action": "charge",
            "params": {"n": 1},
        }
        data, step = await runtime.supervisor._checkpoint_step(task_id)
        assert int(data.position) == 1
        assert step.params["n"] == 2
    finally:
        set_runtime(None)
