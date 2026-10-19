"""POST /approve: чужая сессия, не waiting, повтор и гонка одного execute."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import PlanStep, TaskStatus
from app.web.routers.orchestrator_chat import router
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _change_id(runtime, session: str) -> str:
    approvals = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
    return str(approvals[-1].payload["change_id"])


async def _wait_risk(session: str):
    actions = MockActions(risks={"charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="charge", risk="high", mutating=True, expected="pay")]
        ),
    )
    set_runtime(runtime)
    task_id = await runtime.supervisor.start_task(session, "списать", project_id=13)
    await runtime.supervisor.wait(task_id, timeout=5)
    assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    assert actions.calls == []
    return runtime, actions, task_id, _change_id(runtime, session)


@pytest.mark.asyncio
async def test_approve_foreign_session_is_404(token: str) -> None:
    runtime_a, actions, task_a, id_a = await _wait_risk("claim-foreign-a")
    try:
        task_b = await runtime_a.supervisor.start_task("claim-foreign-b", "списать", project_id=13)
        await runtime_a.supervisor.wait(task_b, timeout=5)
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            foreign = await client.post(
                "/api/orchestrator/chat/claim-foreign-b/approve",
                json={"change_id": id_a, "decision": "approve"},
                headers=_headers(token),
            )
        assert foreign.status_code == 404
        assert foreign.json()["detail"] == "foreign change_id"
        assert actions.calls == []
        assert runtime_a.tasks.get(task_a)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_approve_when_task_is_not_waiting_is_409(token: str) -> None:
    runtime, actions, task_id, change_id = await _wait_risk("claim-not-waiting")
    try:
        runtime.tasks.set_status(task_id, TaskStatus.paused.value)
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/orchestrator/chat/claim-not-waiting/approve",
                json={"change_id": change_id, "decision": "approve"},
                headers=_headers(token),
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "invalid transition"
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.paused.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_repeat_approve_is_409_and_executes_once(token: str) -> None:
    runtime, actions, task_id, change_id = await _wait_risk("claim-repeat")
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/api/orchestrator/chat/claim-repeat/approve",
                json={"change_id": change_id, "decision": "approve"},
                headers=_headers(token),
            )
            assert first.status_code == 200
            await runtime.supervisor.wait(task_id, timeout=5)
            again = await client.post(
                "/api/orchestrator/chat/claim-repeat/approve",
                json={"change_id": change_id, "decision": "deny"},
                headers=_headers(token),
            )
        assert again.status_code == 409
        assert again.json()["detail"] == "change_id already decided"
        assert [call["name"] for call in actions.calls] == ["charge"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_concurrent_approve_executes_once(token: str) -> None:
    """Два одновременных approve одного change_id: execute ровно один, второй — 409."""
    runtime, actions, task_id, change_id = await _wait_risk("claim-race")
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first, second = await asyncio.gather(
                client.post(
                    "/api/orchestrator/chat/claim-race/approve",
                    json={"change_id": change_id, "decision": "approve"},
                    headers=_headers(token),
                ),
                client.post(
                    "/api/orchestrator/chat/claim-race/approve",
                    json={"change_id": change_id, "decision": "approve"},
                    headers=_headers(token),
                ),
            )
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["charge"]
        bodies = [(first.status_code, first.json()), (second.status_code, second.json())]
        codes = sorted([first.status_code, second.status_code])
        assert codes == [200, 409], bodies
        loser = first.json() if first.status_code == 409 else second.json()
        assert loser["detail"] == "change_id already decided", bodies
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)
