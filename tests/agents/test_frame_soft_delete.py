"""Кадр удаляется только при soft_delete и гранте ядра на то же действие."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ActionResult, PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedRouter

_NAMES = ("montage.delete_frame", "montage.delete_child_frame", "delete_child_frame")


class _Frames:
    def __init__(self, *, soft: bool) -> None:
        self.soft = soft
        self.calls: list[str] = []

    def metadata_of(self, name: str) -> dict[str, object]:
        if name not in _NAMES:
            raise KeyError(name)
        return {
            "read_only": False,
            "destructive": True,
            "paid": False,
            "risk": "high",
            "soft_delete": self.soft is True,
        }

    async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
        del params, task_ctx
        self.calls.append(name)
        return ActionResult(ok=True, data={"name": name}, change_id=f"chg-{name}", cost=0.0)


def _runtime(actions: _Frames, name: str):
    return build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=name, mutating=True, risk="high")]),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _NAMES)
async def test_frame_delete_without_soft_delete_is_forbidden(token: str, name: str) -> None:
    del token
    actions = _Frames(soft=False)
    runtime = _runtime(actions, name)
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("frame-hard", "удали", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "deletion_forbidden"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _NAMES)
async def test_frame_delete_soft_without_grant_waits(token: str, name: str) -> None:
    del token
    actions = _Frames(soft=True)
    runtime = _runtime(actions, name)
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("frame-wait", "удали", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _NAMES)
async def test_frame_delete_runs_only_with_soft_delete_and_grant(token: str, name: str) -> None:
    del token
    actions = _Frames(soft=True)
    runtime = _runtime(actions, name)
    set_runtime(runtime)
    session = "frame-go"
    try:
        task_id = await runtime.supervisor.start_task(session, "удали", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        await runtime.supervisor.grant_approval(session)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == [name]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_grant_does_not_run_frame_delete_without_soft_delete(token: str) -> None:
    del token
    actions = _Frames(soft=True)
    runtime = _runtime(actions, "montage.delete_frame")
    set_runtime(runtime)
    session = "frame-flip"
    try:
        task_id = await runtime.supervisor.start_task(session, "удали", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        actions.soft = False
        step = PlanStep(action="montage.delete_frame", mutating=True, risk="high")
        runtime.supervisor._risk_granted.add(runtime.supervisor._risk_grant_key(task_id, 0, step))
        await runtime.supervisor.resume(task_id)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "deletion_forbidden"
    finally:
        set_runtime(None)
