"""WS4: resume_allowed в снимке сессии и в событии смены статуса."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus
from app.web.routers.orchestrator_chat import router as chat_router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter

_TRUE_STATUSES = (
    TaskStatus.paused,
    TaskStatus.waiting_approval,
    TaskStatus.critic_review,
)
_FALSE_STATUSES = (
    TaskStatus.cancelled,
    TaskStatus.done,
    TaskStatus.failed,
    TaskStatus.executing,
    TaskStatus.planning,
    TaskStatus.queued,
)


def _runtime():
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter())
    set_runtime(rt)
    return rt


def _plant(runtime, session: str, status: TaskStatus, *, checkpoint: bool) -> int:
    task_id = runtime.tasks.insert(session, "снимок", status.value)
    runtime.supervisor._session_task[session] = task_id
    if checkpoint:
        runtime.checkpoints.save(
            task_id,
            session,
            CheckpointData(plan=[PlanStep(action="look")], position=0, change_ids=["chg-1"]),
        )
    return task_id


async def _hang() -> None:
    await asyncio.sleep(30)


@pytest.mark.asyncio
async def test_resume_allowed_on_snapshot_and_status_event(token: str) -> None:
    """true только для paused|waiting_approval|critic_review с чекпоинтом и без живого прогона."""
    del token
    runtime = _runtime()
    try:
        for status in _TRUE_STATUSES:
            session = f"ok-{status.value}"
            task_id = _plant(runtime, session, status, checkpoint=True)
            assert runtime.supervisor.resume_allowed(task_id) is True
            snap = runtime.supervisor.session_snapshot(session)
            assert snap["status"] == status.value
            assert snap["resume_allowed"] is True

            bare = f"bare-{status.value}"
            bare_id = _plant(runtime, bare, status, checkpoint=False)
            assert runtime.supervisor.resume_allowed(bare_id) is False

            busy = f"busy-{status.value}"
            busy_id = _plant(runtime, busy, status, checkpoint=True)
            hang = asyncio.create_task(_hang())
            runtime.supervisor._runs[busy_id] = hang
            assert runtime.supervisor.resume_allowed(busy_id) is False
            hang.cancel()

            dead = f"dead-{status.value}"
            dead_id = _plant(runtime, dead, status, checkpoint=True)
            finished = asyncio.create_task(asyncio.sleep(0))
            await finished
            runtime.supervisor._runs[dead_id] = finished
            assert runtime.supervisor.resume_allowed(dead_id) is True

        for status in _FALSE_STATUSES:
            session = f"no-{status.value}"
            task_id = _plant(runtime, session, status, checkpoint=True)
            finished = asyncio.create_task(asyncio.sleep(0))
            await finished
            runtime.supervisor._runs[task_id] = finished
            assert runtime.supervisor.resume_allowed(task_id) is False
            assert runtime.supervisor.session_snapshot(session)["resume_allowed"] is False

        executing = "exec-dead"
        exec_id = _plant(runtime, executing, TaskStatus.executing, checkpoint=True)
        await runtime.supervisor._set_status(exec_id, TaskStatus.paused, session_id=executing)
        paused_events = [
            event
            for event in runtime.bus.replay(executing, 0)
            if event.type == "agent_status" and event.payload.get("status") == "paused"
        ]
        assert paused_events[-1].payload["resume_allowed"] is True

        failed = "exec-fail"
        fail_id = _plant(runtime, failed, TaskStatus.executing, checkpoint=False)
        await runtime.supervisor._set_status(
            fail_id, TaskStatus.failed, session_id=failed, error="x"
        )
        failed_events = [
            event
            for event in runtime.bus.replay(failed, 0)
            if event.type == "agent_status" and event.payload.get("status") == "failed"
        ]
        assert failed_events[-1].payload["resume_allowed"] is False

        app = FastAPI()
        app.add_middleware(OrchestratorAuthMiddleware)
        app.include_router(chat_router, prefix="/api")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            allowed = await client.get(
                "/api/orchestrator/chat/ok-paused/status",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            blocked = await client.get(
                "/api/orchestrator/chat/no-cancelled/status",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            dead_exec = await client.get(
                "/api/orchestrator/chat/no-executing/status",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
        assert allowed.status_code == 200
        assert allowed.json()["resume_allowed"] is True
        assert allowed.json()["status"] == "paused"
        assert blocked.json()["resume_allowed"] is False
        assert blocked.json()["status"] == "cancelled"
        assert dead_exec.json()["resume_allowed"] is False
        assert dead_exec.json()["status"] == "executing"
    finally:
        for task in list(runtime.supervisor._runs.values()):
            if not task.done():
                task.cancel()
        set_runtime(None)
