"""paid_outcome_unknown после рестарта не даёт resume выполнить следующий шаг."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_restart_restores_paid_outcome_unknown(token: str) -> None:
    del token
    paid = PlanStep(
        action="charge",
        params={"_wait": True, "_cost": 1, "_change_id": "chg-1"},
        paid=True,
        mutating=True,
    )
    follow = PlanStep(action="note", mutating=True)
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([paid, follow]),
    )
    set_runtime(runtime)
    session = "paid-restart"
    try:
        task_id = await runtime.supervisor.start_task(session, "платный", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval(session)
        await actions.started.wait()
        runtime.supervisor.request_cancel(session)
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.supervisor._block_reason.get(task_id) == "paid_outcome_unknown"

        fresh = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner([paid, follow]),
        )
        set_runtime(fresh)
        await fresh.supervisor.recover_after_restart()
        assert fresh.supervisor._block_reason.get(task_id) == "paid_outcome_unknown"
        assert task_id in fresh.supervisor._paid_outcome_hold
        await fresh.supervisor.resume(task_id)
        live = fresh.supervisor._runs.get(task_id)
        if live is not None and not live.done():
            await fresh.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["charge"]
        assert fresh.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert fresh.supervisor._block_reason.get(task_id) == "paid_outcome_unknown"
    finally:
        actions.release.set()
        set_runtime(None)
