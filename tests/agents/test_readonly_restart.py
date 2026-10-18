"""Read-only батч не должен оставлять чекпоинт позади orch_tasks.position."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _plan() -> list[PlanStep]:
    return [
        PlanStep(action="save", mutating=True),
        PlanStep(action="look", parallel_group="ro"),
        PlanStep(action="peek", parallel_group="ro"),
        PlanStep(action="hold", params={"_wait": True}),
    ]


@pytest.mark.asyncio
async def test_restart_after_readonly_batch_does_not_rewind_position(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_plan()),
    )
    set_runtime(runtime)
    fresh = None
    try:
        task_id = await runtime.supervisor.start_task("ro-restart", "кадры", project_id=13)
        names = [call["name"] for call in actions.calls]
        for _ in range(50):
            names = [call["name"] for call in actions.calls]
            if "hold" in names:
                break
            await asyncio.sleep(0.02)
        assert names == ["save", "look", "peek", "hold"]
        row = runtime.tasks.get(task_id)
        assert row is not None
        assert int(row["position"]) == 3
        assert row["status"] == TaskStatus.executing.value
        checkpoint = runtime.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        assert checkpoint.position == 3
        assert [call["name"] for call in actions.calls] == ["save", "look", "peek", "hold"]

        fresh = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner(_plan()),
        )
        set_runtime(fresh)
        recovered = await fresh.supervisor.recover_after_restart()
        assert task_id in recovered
        names = [call["name"] for call in actions.calls]
        for _ in range(50):
            names = [call["name"] for call in actions.calls]
            if names.count("hold") >= 2:
                break
            await asyncio.sleep(0.02)
        assert names.count("look") == 1
        assert names.count("peek") == 1
        assert int(fresh.tasks.get(task_id)["position"]) >= 3
        latest = fresh.checkpoints.load_latest(task_id)
        assert latest is not None
        assert latest.position >= 3
    finally:
        for owner in (runtime, fresh):
            if owner is None:
                continue
            job = owner.supervisor._runs.get(task_id)
            if job is not None and not job.done():
                job.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await job
        set_runtime(None)
