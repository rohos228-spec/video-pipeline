"""Находки R0c-B по kill. Ветка cursor/ws3-kill-fixes, не #370."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ActionResult, PlanStep, TaskStatus
from app.services.step_cancel import clear_stop, is_stop_requested
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


class _ThreeWrites(MockActions):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[int] = []

    async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        if name != "write":
            return ActionResult(ok=True, data={"name": name}, change_id=f"chg-{name}", cost=0.0)
        self.rows.append(1)
        self.started.set()
        await self.release.wait()
        self.rows.append(2)
        await asyncio.sleep(0)
        self.rows.append(3)
        return ActionResult(ok=True, data={"name": name}, change_id="chg-write", cost=0.0)


@pytest.mark.asyncio
async def test_interrupt_waits_for_mutating_unpaid_step(token: str) -> None:
    del token
    actions = _ThreeWrites()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="write", mutating=True, paid=False, risk="none"),
                PlanStep(action="later", mutating=True, paid=False, risk="none"),
            ]
        ),
    )
    set_runtime(runtime)
    session = "kill-1a"
    try:
        task_id = await runtime.supervisor.start_task(session, "пиши", project_id=13)
        await actions.started.wait()
        assert actions.rows == [1]
        runtime.supervisor.request_cancel(session)
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.rows == [1, 2, 3]
        assert [call["name"] for call in actions.calls] == ["write"]
        assert runtime.tasks.get(task_id)["status"] in {
            TaskStatus.paused.value,
            TaskStatus.cancelled.value,
        }
        row = runtime.tasks.list_steps(task_id)[0]
        assert row["change_id"] == "chg-write"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_sets_node_stop_key(token: str) -> None:
    """Kill с проектом сразу зовёт request_stop и шлёт stop_key_set=true.

    Ключ — имя файла ``project_{id}.stop`` из ``app.services.step_cancel``.
    """
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="slow", params={"_block": True, "_seconds": 30})]),
    )
    set_runtime(runtime)
    session = "kill-1c"
    try:
        task_id = await runtime.supervisor.start_task(session, "стой", project_id=13)
        await actions.started.wait()
        runtime.supervisor.request_kill(session)
        await runtime.supervisor.wait(task_id, timeout=5)
        kill = next(event for event in runtime.bus.replay(session, 0) if event.type == "kill_switch")
        assert kill.payload["stop_key_set"] is True
        assert kill.payload["stop_key"] == "project_13.stop"
        assert kill.payload["only_node"] is True
        assert is_stop_requested(13)
    finally:
        clear_stop(13)
        set_runtime(None)


@pytest.mark.asyncio
async def test_cancelled_mutating_step_checkpoints_and_warns(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="write", params={"_wait": True}, mutating=True, paid=False)]
        ),
    )
    set_runtime(runtime)
    session = "kill-1b"
    try:
        task_id = await runtime.supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        row = runtime.tasks.list_steps(task_id)[0]
        runtime.tasks.note_running_outcome(int(row["id"]), "chg-partial", 0.0)
        runtime.supervisor._runs[task_id].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        saved = runtime.tasks.list_steps(task_id)[0]
        assert saved["status"] == "partial"
        assert saved["change_id"] == "chg-partial"
        checkpoint = runtime.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        warned = [event for event in runtime.bus.replay(session, 0) if event.type == "partial_write"]
        assert warned[-1].payload["level"] == "WARN"
        assert warned[-1].payload["change_ids"] == ["chg-partial"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
    finally:
        actions.release.set()
        set_runtime(None)


class _MeterRouter(ScriptedRouter):
    def __init__(self) -> None:
        super().__init__()
        self.partial_cost_usd = 0.0

    async def call(self, *args, **kwargs):
        del args, kwargs
        self.partial_cost_usd = 0.37
        self.started.set()
        await asyncio.Event().wait()
        return None


@pytest.mark.asyncio
async def test_cancelled_llm_step_records_spent_tokens(token: str) -> None:
    del token
    router = _MeterRouter()
    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router),
    )
    set_runtime(runtime)
    session = "kill-3b"
    try:
        task_id = await runtime.supervisor.start_task(session, "план")
        await router.started.wait()
        runtime.supervisor._runs[task_id].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
        assert float(runtime.tasks.get(task_id)["spend_internal"] or 0) == pytest.approx(0.37)
    finally:
        set_runtime(None)
