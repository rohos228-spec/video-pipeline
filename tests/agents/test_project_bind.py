"""POST /api/orchestrator/chat/{session}/project снимает missing_project_id."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import _plan_fingerprint
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import CheckpointData, PlanStep, SpendSnapshot, TaskStatus
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _row(runtime, task_id: int) -> dict:
    row = runtime.tasks.get(task_id)
    return {"status": row["status"], "project_id": row["project_id"]}


@pytest.mark.asyncio
async def test_project_route_binds_id_and_reaches_step(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="edit", mutating=True)]),
    )
    set_runtime(runtime)
    session = "bind-project"
    try:
        task_id = await runtime.supervisor.start_task(session, "без проекта")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.tasks.get(task_id)["project_id"] is None
        assert actions.calls == []
        held = _row(runtime, task_id)
        await runtime.supervisor.grant_approval(session)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert _row(runtime, task_id) == held
        assert actions.calls == []
        approvals = [
            event
            for event in runtime.bus.replay(session, 0)
            if event.type == "approval_required" and event.payload.get("reason") == "missing_project_id"
        ]
        assert approvals
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bound = await client.post(
                f"/api/orchestrator/chat/{session}/project",
                json={"project_id": 13},
                headers=_headers(),
            )
        assert bound.status_code == 200
        body = bound.json()
        assert body["ok"] is True
        assert body["session"] == session
        assert body["task_id"] == task_id
        assert body["project_id"] == 13
        assert isinstance(body["status"], str) and body["status"]
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["project_id"] == 13
        assert [call["name"] for call in actions.calls] == ["edit"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert runtime.supervisor._block_reason.get(task_id) != "missing_project_id"
        assert TEST_TOKEN not in bound.text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_project_route_rejects_task_that_is_not_active(token: str) -> None:
    """project_id пишется только в активную задачу сессии.

    Вторая задача той же сессии ждёт risk. Тело с task_id первой не
    снимает missing_project_id и не одобряет риск второй.
    """
    del token
    actions = MockActions(risks={"edit": "none", "charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="edit", mutating=True)]),
    )
    set_runtime(runtime)
    session = "bind-two-tasks"
    try:
        held_id = await runtime.supervisor.start_task(session, "без проекта")
        await runtime.supervisor.wait(held_id, timeout=5)
        assert runtime.tasks.get(held_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.supervisor._block_reason.get(held_id) == "missing_project_id"
        held_before = _row(runtime, held_id)

        runtime.supervisor.planner = FixedPlanner(
            [PlanStep(action="charge", risk="low", paid=True, mutating=True, estimated_cost=1.0, params={"_cost": 1})]
        )
        risk_id = await runtime.supervisor.start_task(session, "риск", project_id=13)
        await runtime.supervisor.wait(risk_id, timeout=5)
        assert runtime.supervisor.active_task(session) == risk_id
        assert runtime.tasks.get(risk_id)["status"] == TaskStatus.waiting_approval.value
        risk_reasons = [
            event.payload.get("reason")
            for event in runtime.bus.replay(session, 0)
            if event.type == "approval_required" and event.task_id == risk_id
        ]
        assert risk_reasons == ["risk"]
        risk_before = _row(runtime, risk_id)
        assert actions.calls == []

        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/orchestrator/chat/{session}/project",
                json={"project_id": 13, "task_id": held_id},
                headers=_headers(),
            )
        assert response.status_code == 409
        assert _row(runtime, held_id) == held_before
        assert _row(runtime, risk_id) == risk_before
        assert runtime.supervisor._block_reason.get(held_id) == "missing_project_id"
        assert actions.calls == []
        assert float(runtime.tasks.get(risk_id)["spend_actions"] or 0) == 0
        assert TEST_TOKEN not in response.text
    finally:
        set_runtime(None)


def _approval_ids(runtime, session: str, task_id: int) -> list[str]:
    return [
        str(event.payload.get("change_id"))
        for event in runtime.bus.replay(session, 0)
        if event.type == "approval_required" and event.task_id == task_id and event.payload.get("change_id")
    ]


@pytest.mark.asyncio
async def test_project_bind_holds_risky_paid_step_for_a_new_approval(token: str) -> None:
    """Привязка снимает только missing_project_id.

    Одобренный чекпоинт платного шага не получает грант риска: после
    POST /project задача снова ждёт, вызов и spend не начинаются, change_id новый.
    """
    del token
    actions = MockActions(risks={"charge": "high", "edit": "none"})
    charge = PlanStep(
        action="charge",
        risk="low",
        paid=True,
        mutating=True,
        estimated_cost=1.0,
        params={"_cost": 1},
    )
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([charge]),
    )
    set_runtime(runtime)
    session = "bind-paid"
    try:
        task_id = await runtime.supervisor.start_task(session, "без проекта")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.supervisor._block_reason.get(task_id) == "missing_project_id"
        before_ids = _approval_ids(runtime, session, task_id)
        assert before_ids
        runtime.checkpoints.save(
            task_id,
            session,
            CheckpointData(plan=[charge], position=0, spend=SpendSnapshot()),
        )
        runtime.supervisor._approved[task_id] = _plan_fingerprint([charge])
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bound = await client.post(
                f"/api/orchestrator/chat/{session}/project",
                json={"project_id": 13},
                headers=_headers(),
            )
        assert bound.status_code == 200
        assert bound.json()["status"] == TaskStatus.waiting_approval.value
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.tasks.get(task_id)["project_id"] == 13
        assert runtime.supervisor._block_reason.get(task_id) == "risk"
        assert actions.calls == []
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 0
        assert runtime.supervisor._risk_granted == set()
        after_ids = _approval_ids(runtime, session, task_id)
        assert after_ids[0] == before_ids[0]
        assert len(after_ids) == len(before_ids) + 1
        fresh = after_ids[-1]
        assert fresh != before_ids[-1]
        assert fresh.startswith(f"orch-appr-{task_id}-")
        assert TEST_TOKEN not in bound.text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_project_route_rejects_bad_id_and_other_states(token: str) -> None:
    del token
    actions = MockActions(risks={"charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="charge", risk="low", mutating=True, expected="pay")]),
    )
    set_runtime(runtime)
    try:
        missing_actions = MockActions()
        missing = build_runtime(
            actions=missing_actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner([PlanStep(action="edit", mutating=True)]),
        )
        set_runtime(missing)
        session = "bind-422"
        task_id = await missing.supervisor.start_task(session, "без проекта")
        await missing.supervisor.wait(task_id, timeout=5)
        before = _row(missing, task_id)
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for payload in (
                {"project_id": 0},
                {"project_id": -1},
                {"project_id": False},
                {"project_id": "13"},
                {"project_id": 13.0},
                {"project_id": True},
                {"project_id": 13, "task_id": "13"},
                {"project_id": 13, "task_id": 13.0},
                {"project_id": 13, "task_id": True},
            ):
                response = await client.post(
                    f"/api/orchestrator/chat/{session}/project",
                    json=payload,
                    headers=_headers(),
                )
                assert response.status_code == 422
                assert _row(missing, task_id) == before
                assert TEST_TOKEN not in response.text

        set_runtime(runtime)
        risk_id = await runtime.supervisor.start_task("bind-risk", "риск", project_id=13)
        await runtime.supervisor.wait(risk_id, timeout=5)
        assert runtime.tasks.get(risk_id)["status"] == TaskStatus.waiting_approval.value
        risk_before = _row(runtime, risk_id)
        readonly = build_runtime(
            actions=MockActions(),
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner([PlanStep(action="look")]),
        )
        set_runtime(readonly)
        done_id = await readonly.supervisor.start_task("bind-done", "чтение")
        await readonly.supervisor.wait(done_id, timeout=5)
        assert readonly.tasks.get(done_id)["status"] == TaskStatus.done.value
        done_before = _row(readonly, done_id)
        failed = build_runtime(
            actions=MockActions(),
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner([PlanStep(action="delete_child_frame", mutating=True)]),
        )
        set_runtime(failed)
        failed_id = await failed.supervisor.start_task("bind-failed", "удалить", project_id=13)
        await failed.supervisor.wait(failed_id, timeout=5)
        assert failed.tasks.get(failed_id)["status"] == TaskStatus.failed.value
        failed_before = _row(failed, failed_id)

        set_runtime(runtime)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            risk = await client.post(
                "/api/orchestrator/chat/bind-risk/project",
                json={"project_id": 99},
                headers=_headers(),
            )
            assert risk.status_code == 409
            assert _row(runtime, risk_id) == risk_before

        set_runtime(readonly)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            done = await client.post(
                "/api/orchestrator/chat/bind-done/project",
                json={"project_id": 13},
                headers=_headers(),
            )
            foreign = await client.post(
                "/api/orchestrator/chat/bind-done/project",
                json={"project_id": 13, "task_id": risk_id},
                headers=_headers(),
            )
        assert done.status_code == 409
        assert foreign.status_code == 409
        assert _row(readonly, done_id) == done_before
        assert _row(runtime, risk_id) == risk_before

        set_runtime(failed)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            blocked = await client.post(
                "/api/orchestrator/chat/bind-failed/project",
                json={"project_id": 13},
                headers=_headers(),
            )
        assert blocked.status_code == 409
        assert _row(failed, failed_id) == failed_before
        assert missing_actions.calls == []
    finally:
        set_runtime(None)
