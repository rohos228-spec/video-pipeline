"""Блокер ревью #350: kill отменяет задачи, которые ещё стоят в очереди."""

from __future__ import annotations

import asyncio

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_kill_cancels_queued_tasks(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="slow", params={"_block": True, "_seconds": 30})]),
    )
    set_runtime(runtime)
    try:
        active = await runtime.supervisor.start_task("kill-q", "идёт", project_id=13)
        await actions.started.wait()
        queued = await runtime.supervisor.submit_task("kill-q", "потом", project_id=13)
        assert runtime.tasks.get(queued)["status"] == TaskStatus.queued.value
        runtime.supervisor.request_kill("kill-q")
        pending = [job for job in list(runtime.supervisor._shield_jobs) if not job.done()]
        if pending:
            await asyncio.wait(pending)
        assert runtime.supervisor._queued.get("kill-q", []) == []
        assert runtime.tasks.get(queued)["status"] == TaskStatus.cancelled.value
        assert runtime.tasks.list_steps(queued) == []
        runtime.supervisor._kill.discard("kill-q")
        await runtime.supervisor._start_queued("kill-q", active)
        assert queued not in runtime.supervisor._runs
        assert runtime.tasks.get(queued)["status"] == TaskStatus.cancelled.value
        run = runtime.supervisor._runs.get(active)
        if run is not None and not run.done():
            run.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
    finally:
        set_runtime(None)
