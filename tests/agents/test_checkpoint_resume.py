"""Resume с последнего чекпоинта не переигрывает уже сделанный шаг."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_resume_continues_after_checkpoint(token: str) -> None:
    del token
    session = "resume-1"
    actions = MockActions()
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
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(steps),
    )

    async def _pause_after_first(name: str, params: dict, ctx: dict) -> None:
        del params, ctx
        if name == "keep":
            rt.supervisor.request_pause(session)

    actions.on_execute = _pause_after_first
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(session, "доделать")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.paused.value
        checkpoint = rt.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        assert checkpoint.position == 1
        assert checkpoint.change_ids == ["chg-keep"]
        assert [call["name"] for call in actions.calls] == ["keep"]
        assert rt.tasks.list_steps(task_id)[0]["status"] == "done"
        assert rt.tasks.list_steps(task_id)[0]["change_id"] == "chg-keep"

        resumed = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner(steps),
        )
        set_runtime(resumed)
        await resumed.supervisor.resume(task_id)
        await resumed.supervisor.wait(task_id, timeout=5)
        assert resumed.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["keep", "next"]
        latest = resumed.checkpoints.load_latest(task_id)
        assert latest is not None
        assert latest.change_ids == ["chg-keep", "chg-next"]
        assert latest.position == 2
        rows = resumed.tasks.list_steps(task_id)
        assert [row["action_name"] for row in rows] == ["keep", "next"]
        assert rows[0]["change_id"] == "chg-keep"
        assert rows[0]["status"] == "done"
    finally:
        set_runtime(None)
