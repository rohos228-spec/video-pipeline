"""Находки R0c-B по kill. Ветка cursor/ws3-kill-fixes, не #370."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import (
    ActionResult,
    ApprovalDecision,
    CheckpointData,
    PlanStep,
    TaskStatus,
)
from app.services.step_cancel import abort_if_cancelled, clear_stop, is_stop_requested, request_stop
from app.web.routers.orchestrator_chat import router as chat_router
from tests.agents.conftest import TEST_TOKEN
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
async def test_kill_records_real_change_id_after_mutating_release(token: str) -> None:
    """Kill не обрывает inner. После release на partial лежит настоящий change_id."""
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="write", params={"_wait": True}, mutating=True, paid=False),
                PlanStep(action="later", mutating=True, paid=False),
            ]
        ),
    )
    set_runtime(runtime)
    session = "kill-late-id"
    _delay_partial_publish(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill(session)
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        actions.release.set()
        saved = await _partial_row(runtime, task_id)
        assert saved["status"] == "partial"
        assert saved["change_id"] == "chg-write-1"
        assert [call["name"] for call in actions.calls] == ["write"]
        assert runtime.supervisor.list_partial_change_ids(task_id) == ["chg-write-1"]
        warned = [event for event in runtime.bus.replay(session, 0) if event.type == "partial_write"]
        assert len(warned) == 1
        assert warned[-1].payload["change_ids"] == ["chg-write-1"]
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_inner_done_before_kill_keeps_change_id_and_resume_does_not_rewrite(token: str) -> None:
    """K1: inner уже done, callback ещё не видит _shield_orphaned. partial с реальным id."""
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="write", params={"_wait": True}, mutating=True, paid=False),
                PlanStep(action="later", mutating=True, paid=False),
            ]
        ),
    )
    set_runtime(runtime)
    session = "kill-k1"
    supervisor = runtime.supervisor
    original = supervisor._on_mutating_done

    def _kill_as_soon_as_inner_finishes(done, **kwargs):
        original(done, **kwargs)
        supervisor.request_kill(session)

    supervisor._on_mutating_done = _kill_as_soon_as_inner_finishes
    try:
        task_id = await supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        actions.release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor.wait(task_id, timeout=5)
        saved = await _partial_row(runtime, task_id)
        assert saved["status"] == "partial"
        assert saved["change_id"] == "chg-write-1"
        warned = [event for event in runtime.bus.replay(session, 0) if event.type == "partial_write"]
        assert warned[-1].payload["change_ids"] == ["chg-write-1"]
        await supervisor.resume(task_id)
        await supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["write"]
    finally:
        actions.release.set()
        set_runtime(None)


def _delay_partial_publish(runtime) -> None:
    """publish partial_write уступает циклу: пустой WARN не должен обогнать id."""
    original = runtime.bus.publish

    async def publish(session_id, event_type, payload, **kwargs):
        if event_type == "partial_write":
            await asyncio.sleep(0.05)
        return await original(session_id, event_type, payload, **kwargs)

    runtime.bus.publish = publish


async def _partial_row(runtime, task_id: int) -> dict:
    for _ in range(50):
        row = runtime.tasks.list_steps(task_id)[0]
        if row.get("change_id"):
            return row
        await asyncio.sleep(0.01)
    return runtime.tasks.list_steps(task_id)[0]


@pytest.mark.asyncio
async def test_resume_clears_stop_and_other_session_keeps_running(token: str) -> None:
    """Kill → resume снимает project_*.stop. Вторая сессия того же проекта жива."""
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
    try:
        clear_stop(13)
        task_a = await runtime.supervisor.start_task("stop-a", "рви", project_id=13)
        await actions.started.wait()
        task_b = await runtime.supervisor.start_task("stop-b", "рядом", project_id=13)
        await _until_calls(actions, 2)
        published = runtime.supervisor.request_kill("stop-a")
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_a, timeout=5)
        assert is_stop_requested(13)
        assert runtime.tasks.get(task_b)["status"] == TaskStatus.executing.value
        assert runtime.supervisor.resume_allowed(task_a) is True
        await runtime.supervisor.resume(task_a)
        await runtime.supervisor.wait(task_a, timeout=5)
        assert is_stop_requested(13) is False
        abort_if_cancelled(13)
        assert runtime.tasks.get(task_b)["status"] == TaskStatus.executing.value
    finally:
        actions.release.set()
        clear_stop(13)
        set_runtime(None)


@pytest.mark.asyncio
async def test_grant_clears_project_stop(token: str) -> None:
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
    session = "stop-grant"
    try:
        clear_stop(13)
        task_id = await runtime.supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill(session)
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        assert is_stop_requested(13)
        await runtime.supervisor.grant_approval(session)
        assert is_stop_requested(13) is False
        abort_if_cancelled(13)
    finally:
        actions.release.set()
        clear_stop(13)
        set_runtime(None)


@pytest.mark.asyncio
async def test_user_stop_survives_grant_and_resume(token: str) -> None:
    """Чужой user_stop не снимается grant/resume задачи того же проекта."""
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
    try:
        clear_stop(13)
        request_stop(13, reason="user_stop")
        task_id = await runtime.supervisor.start_task("user-stop", "рви", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill("user-stop")
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval("user-stop")
        assert is_stop_requested(13) is True
        clear_stop(13)
        request_stop(13, reason="user_stop")
        task_resume = await runtime.supervisor.start_task("user-stop-2", "рви", project_id=13)
        await _until_calls(actions, 2)
        published = runtime.supervisor.request_kill("user-stop-2")
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_resume, timeout=5)
        await runtime.supervisor.resume(task_resume)
        assert is_stop_requested(13) is True
    finally:
        actions.release.set()
        clear_stop(13)
        set_runtime(None)


@pytest.mark.asyncio
async def test_resume_of_one_kill_leaves_the_other_kill_stop(token: str) -> None:
    """kill A + kill B: resume A не снимает stop, пока B ещё держит его."""
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
    try:
        clear_stop(13)
        task_a = await runtime.supervisor.start_task("kill-a", "рви", project_id=13)
        await actions.started.wait()
        task_b = await runtime.supervisor.start_task("kill-b", "рви", project_id=13)
        await _until_calls(actions, 2)
        published = runtime.supervisor.request_kill("kill-a")
        if published is not None:
            await published
        published_b = runtime.supervisor.request_kill("kill-b")
        if published_b is not None:
            await published_b
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_a, timeout=5)
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_b, timeout=5)
        assert is_stop_requested(13)
        await runtime.supervisor.resume(task_a)
        assert is_stop_requested(13) is True
    finally:
        actions.release.set()
        clear_stop(13)
        set_runtime(None)


async def _until_calls(actions: MockActions, count: int) -> None:
    for _ in range(50):
        if len(actions.calls) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(len(actions.calls))


@pytest.mark.asyncio
async def test_cancelled_mutating_step_checkpoints_and_warns(token: str) -> None:
    """request_kill, без подложенного change_id и без прямого _runs.cancel."""
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
    _delay_partial_publish(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill(session)
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        actions.release.set()
        saved = await _partial_row(runtime, task_id)
        assert saved["status"] == "partial"
        assert saved["change_id"] == "chg-write-1"
        assert [call["name"] for call in actions.calls] == ["write"]
        checkpoint = runtime.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        warned = [event for event in runtime.bus.replay(session, 0) if event.type == "partial_write"]
        assert len(warned) == 1
        assert warned[-1].payload["level"] == "WARN"
        assert warned[-1].payload["change_ids"] == ["chg-write-1"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.supervisor.resume_allowed(task_id) is True
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_then_resume_is_not_rejected(token: str) -> None:
    """partial после kill — waiting_approval, resume не 409."""
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
    session = "kill-resume"
    try:
        task_id = await runtime.supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill(session)
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        actions.release.set()
        await _partial_row(runtime, task_id)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.supervisor.resume_allowed(task_id) is True
        await runtime.supervisor.resume(task_id)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["write"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.supervisor._block_reason.get(task_id) == "partial_write"
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_approve_partial_closes_step_and_runs_the_next(token: str) -> None:
    """approve принимает partial: write один раз, следующий шаг идёт, задача done."""
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="write", params={"_wait": True}, mutating=True, paid=False),
                PlanStep(action="later", mutating=True, paid=False),
            ]
        ),
    )
    set_runtime(runtime)
    session = "partial-accept"
    try:
        task_id = await runtime.supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill(session)
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        actions.release.set()
        saved = await _partial_row(runtime, task_id)
        assert saved["change_id"] == "chg-write-1"
        await runtime.supervisor.grant_approval(session, ApprovalDecision.approve)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["write", "later"]
        rows = runtime.tasks.list_steps(task_id)
        write_row = next(row for row in rows if row["action_name"] == "write")
        assert write_row["status"] == "done"
        assert write_row["change_id"] == "chg-write-1"
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        actions.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_deny_partial_fails_task_and_keeps_the_row(token: str) -> None:
    """deny на partial_write валиден и не стирает change_id."""
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
    session = "partial-deny"
    try:
        task_id = await runtime.supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill(session)
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        actions.release.set()
        saved = await _partial_row(runtime, task_id)
        assert saved["change_id"] == "chg-write-1"
        await runtime.supervisor.grant_approval(session, ApprovalDecision.deny)
        assert [call["name"] for call in actions.calls] == ["write"]
        rows = runtime.tasks.list_steps(task_id)
        assert rows[0]["status"] == "partial"
        assert rows[0]["change_id"] == "chg-write-1"
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
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


_HOLD_PLAN = (
    '{"steps":[{"action":"hold","params":{"_wait":true},"expected":"ok",'
    '"paid":false,"mutating":false,"risk":"none"}]}'
)


class _SplitMeter(ScriptedRouter):
    """Второй вызов держит partial_cost_usd, первый отдаёт план сразу."""

    def __init__(self) -> None:
        super().__init__()
        self.partial_cost_usd = 0.0
        self.second = asyncio.Event()
        self.release = asyncio.Event()
        self.script = [_HOLD_PLAN, _HOLD_PLAN]

    async def call(self, *args, **kwargs):
        if self.call_count >= 1:
            self.partial_cost_usd = 0.37
            self.second.set()
            await self.release.wait()
            self.partial_cost_usd = 0.0
        return await super().call(*args, **kwargs)


@pytest.mark.asyncio
async def test_cancelled_task_does_not_take_another_tasks_partial_cost(token: str) -> None:
    del token
    router = _SplitMeter()
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router),
    )
    set_runtime(runtime)
    try:
        task_a = await runtime.supervisor.start_task("cost-a", "план")
        await actions.started.wait()
        task_b = await runtime.supervisor.start_task("cost-b", "план")
        await router.second.wait()
        runtime.supervisor._runs[task_a].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_a, timeout=5)
        assert float(runtime.tasks.get(task_a)["spend_internal"] or 0) == 0
        assert router.partial_cost_usd == pytest.approx(0.37)
        runtime.supervisor._runs[task_b].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_b, timeout=5)
        assert float(runtime.tasks.get(task_a)["spend_internal"] or 0) == 0
        assert float(runtime.tasks.get(task_b)["spend_internal"] or 0) == pytest.approx(0.37)
        assert router.partial_cost_usd == 0
    finally:
        router.release.set()
        set_runtime(None)


@pytest.mark.asyncio
async def test_rerun_after_cancelled_mutating_step_is_idempotent(token: str) -> None:
    del token
    actions = MockActions()
    step = PlanStep(
        action="write",
        params={"idempotency_key": "k1"},
        mutating=True,
        paid=False,
    )
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(runtime)
    session = "kill-4b"
    try:
        task_id = runtime.tasks.insert(session, "повтор", TaskStatus.paused.value, project_id=13)
        runtime.tasks.save_plan(task_id, [step], position=0)
        step_id = runtime.tasks.insert_step(task_id, 0, step)
        runtime.tasks.finish_step(step_id, "partial", "chg-once", 0.0)
        CheckpointStore().save(task_id, session, CheckpointData(plan=[step], position=0))
        await runtime.supervisor.resume(task_id)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        rows = runtime.tasks.list_steps(task_id)
        assert [row["change_id"] for row in rows] == ["chg-once"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.supervisor._block_reason.get(task_id) == "partial_write"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_partial_write_marker_lists_change_ids(token: str) -> None:
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
    session = "kill-2"
    _delay_partial_publish(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, "частично", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill(session)
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        actions.release.set()
        saved = await _partial_row(runtime, task_id)
        assert saved["change_id"] == "chg-write-1"
        assert [call["name"] for call in actions.calls] == ["write"]
        assert runtime.supervisor.list_partial_change_ids(task_id) == ["chg-write-1"]
        app = FastAPI()
        app.add_middleware(OrchestratorAuthMiddleware)
        app.include_router(chat_router, prefix="/api")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get(
                f"/api/orchestrator/chat/{session}/partial-changes",
                params={"task_id": task_id},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
        assert listed.status_code == 200
        assert listed.json()["change_ids"] == ["chg-write-1"]
        assert TEST_TOKEN not in listed.text
        warned = [event for event in runtime.bus.replay(session, 0) if event.type == "partial_write"]
        assert len(warned) == 1
        assert warned[-1].payload["change_ids"] == ["chg-write-1"]
    finally:
        actions.release.set()
        set_runtime(None)
