"""HTTP POST /api/orchestrator/chat/{session_id}/approve."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus, forecast_cost
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

_FIELDS = {"executed", "reason", "forecast_usd", "retry_armed", "change_id", "decision"}


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _insert_paid(session: str, *, cost: float = 4.25) -> tuple[int, PlanStep]:
    step = PlanStep(
        action="charge",
        params={"_cost": 1.5},
        expected="paid",
        paid=True,
        mutating=True,
        estimated_cost=cost,
    )
    tasks = TaskStore()
    task_id = tasks.insert(session, "списать", TaskStatus.executing.value, project_id=13)
    tasks.save_plan(task_id, [step], position=0)
    tasks.insert_step(task_id, 0, step)
    CheckpointStore().save(task_id, session, CheckpointData(plan=[step], position=0))
    return task_id, step


def _change_id(runtime, session: str) -> str:
    approvals = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
    return str(approvals[-1].payload["change_id"])


def _effects(runtime, session: str, task_id: int) -> dict:
    return {
        "calls": [call["name"] for call in runtime.supervisor.actions.calls],
        "status": runtime.tasks.get(task_id)["status"],
        "events": len(runtime.bus.replay(session, 0)),
    }


@pytest.mark.asyncio
async def test_approve_route_happy_path(token: str) -> None:
    del token
    session = "approve-happy"
    task_id, step = _insert_paid(session)
    actions = MockActions()
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        change_id = _change_id(runtime, session)
        forecast = forecast_cost([step])
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            armed = await client.post(
                f"/api/orchestrator/chat/{session}/approve",
                json={"change_id": change_id, "decision": "retry_step"},
                headers=_headers(),
            )
            assert armed.status_code == 200
            body = armed.json()
            assert set(body) == _FIELDS
            assert body["executed"] is False
            assert body["forecast_usd"] == forecast == 4.25
            assert body["retry_armed"] is True
            assert body["change_id"] == change_id
            assert body["decision"] == "retry_step"
            assert actions.calls == []

            started = await client.post(
                f"/api/orchestrator/chat/{session}/approve",
                json={"change_id": change_id, "retry_grant": True},
                headers=_headers(),
            )
            assert started.status_code == 200
            assert started.json()["executed"] is True
            assert started.json()["change_id"] == change_id
            await runtime.supervisor.wait(task_id, timeout=5)
            assert [call["name"] for call in actions.calls] == ["charge"]
            assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value

            spent = await client.post(
                f"/api/orchestrator/chat/{session}/approve",
                json={"change_id": change_id, "retry_grant": True},
                headers=_headers(),
            )
            assert spent.status_code == 200
            assert spent.json()["reason"] == "retry_grant_spent"
            assert spent.json()["executed"] is False
            assert [call["name"] for call in actions.calls] == ["charge"]

            risk = MockActions(risks={"charge": "high"})
            risk_runtime = build_runtime(
                actions=risk,
                validators=MockValidators(),
                router=ScriptedRouter(),
                planner=FixedPlanner(
                    [PlanStep(action="charge", risk="low", mutating=True, expected="pay")]
                ),
            )
            set_runtime(risk_runtime)
            risk_id = await risk_runtime.supervisor.start_task("approve-old", "списать", project_id=13)
            await risk_runtime.supervisor.wait(risk_id, timeout=5)
            assert risk.calls == []
            old = await client.post(
                "/api/orchestrator/chat/approve-old/approve",
                json={"decision": None},
                headers=_headers(),
            )
            assert old.status_code == 200
            old_body = old.json()
            assert set(old_body) == _FIELDS
            assert old_body["decision"] is None
            assert old_body["retry_armed"] is False
            assert old_body["change_id"] == _change_id(risk_runtime, "approve-old")
            await risk_runtime.supervisor.wait(risk_id, timeout=5)
            assert [call["name"] for call in risk.calls] == ["charge"]
            assert risk_runtime.tasks.get(risk_id)["status"] == TaskStatus.done.value
            assert TEST_TOKEN not in armed.text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_approve_route_404_409_passthrough(token: str) -> None:
    del token
    session_a = "approve-404-a"
    session_b = "approve-404-b"
    task_a, _step = _insert_paid(session_a)
    task_b, _step_b = _insert_paid(session_b, cost=1.0)
    actions = MockActions()
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_a, timeout=5)
        await runtime.supervisor.wait(task_b, timeout=5)
        id_a = _change_id(runtime, session_a)
        id_b = _change_id(runtime, session_b)
        assert id_a != id_b
        before_a = _effects(runtime, session_a, task_a)
        before_b = _effects(runtime, session_b, task_b)
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unknown = await client.post(
                f"/api/orchestrator/chat/{session_a}/approve",
                json={"change_id": "made-up", "decision": "approve"},
                headers=_headers(),
            )
            foreign = await client.post(
                f"/api/orchestrator/chat/{session_b}/approve",
                json={"change_id": id_a, "decision": "approve"},
                headers=_headers(),
            )
            assert unknown.status_code == 404
            assert unknown.json()["detail"] == "unknown change_id"
            assert foreign.status_code == 404
            assert foreign.json()["detail"] == "foreign change_id"
            assert _effects(runtime, session_a, task_a) == before_a
            assert _effects(runtime, session_b, task_b) == before_b
            assert actions.calls == []

            granted = await client.post(
                f"/api/orchestrator/chat/{session_a}/approve",
                json={"change_id": id_a, "decision": "approve"},
                headers=_headers(),
            )
            assert granted.status_code == 200
            await runtime.supervisor.wait(task_a, timeout=5)
            settled = _effects(runtime, session_a, task_a)
            again = await client.post(
                f"/api/orchestrator/chat/{session_a}/approve",
                json={"change_id": id_a, "decision": "approve"},
                headers=_headers(),
            )
            assert again.status_code == 409
            assert again.json()["detail"] == "change_id already decided"
            assert _effects(runtime, session_a, task_a) == settled
            assert TEST_TOKEN not in unknown.text
            assert TEST_TOKEN not in foreign.text
            assert TEST_TOKEN not in again.text
            assert "ORCH_API_TOKEN" not in again.text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_approve_route_invalid_transition_is_409(token: str) -> None:
    """Повторный grant без change_id после done — 409, не 500, и без записи."""
    del token
    session = "approve-not-waiting"
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look", risk="none", expected="see")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task(session, "посмотреть", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        before = _effects(runtime, session, task_id)
        decided = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_decided"]
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/orchestrator/chat/{session}/approve",
                json={},
                headers=_headers(),
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "invalid transition"
        assert _effects(runtime, session, task_id) == before
        assert [event for event in runtime.bus.replay(session, 0) if event.type == "approval_decided"] == decided
        assert [call["name"] for call in actions.calls] == ["look"]
        assert TEST_TOKEN not in response.text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_approve_route_invalid_body_422(token: str) -> None:
    del token
    session = "approve-422"
    task_id, _step = _insert_paid(session)
    actions = MockActions()
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        await runtime.supervisor.wait(task_id, timeout=5)
        before = _effects(runtime, session, task_id)
        bodies = (
            {"decision": "ship_it"},
            {"decision": "approve", "note": "extra"},
            {"retry_grant": {"enabled": True}},
            {"change_id": 12},
        )
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for body in bodies:
                response = await client.post(
                    f"/api/orchestrator/chat/{session}/approve",
                    json=body,
                    headers=_headers(),
                )
                assert response.status_code == 422
                assert _effects(runtime, session, task_id) == before
                assert TEST_TOKEN not in response.text
                assert "ORCH_API_TOKEN" not in response.text
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


def _approval_payloads(runtime, session: str) -> list[dict]:
    return [event.payload for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]


@pytest.mark.asyncio
async def test_retry_armed_value_lifecycle(token: str) -> None:
    del token
    sessions = ("life-retry", "life-approve", "life-deny")
    tasks = {session: _insert_paid(session)[0] for session in sessions}
    actions = MockActions()
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        await runtime.supervisor.recover_after_restart()
        for task_id in tasks.values():
            await runtime.supervisor.wait(task_id, timeout=5)
        for session in sessions:
            assert _approval_payloads(runtime, session)[-1]["retry_armed"] is False
        runtime.supervisor._retry_armed[tasks["life-approve"]] = "stale"
        runtime.supervisor._retry_armed[tasks["life-deny"]] = "stale"
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            retry_id = _change_id(runtime, "life-retry")
            armed = await client.post(
                "/api/orchestrator/chat/life-retry/approve",
                json={"change_id": retry_id, "decision": "retry_step"},
                headers=_headers(),
            )
            assert armed.status_code == 200
            assert armed.json()["retry_armed"] is True
            assert _approval_payloads(runtime, "life-retry")[-1]["retry_armed"] is True
            assert runtime.supervisor._retry_armed.get(tasks["life-retry"])
            assert actions.calls == []

            consumed = await client.post(
                "/api/orchestrator/chat/life-retry/approve",
                json={"change_id": retry_id, "retry_grant": True},
                headers=_headers(),
            )
            assert consumed.status_code == 200
            assert consumed.json()["retry_armed"] is False
            assert consumed.json()["executed"] is True
            assert tasks["life-retry"] not in runtime.supervisor._retry_armed

            approved = await client.post(
                "/api/orchestrator/chat/life-approve/approve",
                json={"change_id": _change_id(runtime, "life-approve"), "decision": "approve"},
                headers=_headers(),
            )
            denied = await client.post(
                "/api/orchestrator/chat/life-deny/approve",
                json={"change_id": _change_id(runtime, "life-deny"), "decision": "deny"},
                headers=_headers(),
            )
            assert approved.status_code == 200
            assert denied.status_code == 200
            assert approved.json()["retry_armed"] is False
            assert denied.json()["retry_armed"] is False
            assert tasks["life-approve"] not in runtime.supervisor._retry_armed
            assert tasks["life-deny"] not in runtime.supervisor._retry_armed
            await runtime.supervisor.wait(tasks["life-retry"], timeout=5)
            assert [call["name"] for call in actions.calls] == ["charge"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_approve_route_401_without_token(token: str) -> None:
    del token
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post(
            "/api/orchestrator/chat/approve-401/approve",
            json={"decision": "approve"},
        )
        wrong = await client.post(
            "/api/orchestrator/chat/approve-401/approve",
            json={"decision": "approve"},
            headers={"Authorization": "Bearer not-the-token"},
        )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["detail"] == "unauthorized"
    assert TEST_TOKEN not in missing.text
    assert "ORCH_API_TOKEN" not in missing.text
