"""Имя вне белого списка нельзя одобрить и нельзя выполнить."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import ApprovalGrantError
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

_FORBIDDEN = ("exec", "edit_files", "fix_bugs")


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


class _ClosedRegistry:
    """Закрытый реестр: неизвестное имя — KeyError, известное может притвориться безопасным."""

    def __init__(self, safe: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.safe = safe or set()

    def metadata_of(self, name: str) -> dict[str, object]:
        if name not in self.safe:
            raise KeyError(name)
        return {
            "name": name,
            "risk": "none",
            "read_only": True,
            "destructive": False,
            "paid": False,
        }

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del params, task_ctx
        self.calls.append(name)
        return {"ok": True, "data": {}, "change_id": None, "cost": 0.0}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _FORBIDDEN)
async def test_forbidden_action_is_not_executed(token: str, action: str) -> None:
    del token
    actions = _ClosedRegistry()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=action, risk="none")]),
    )
    set_runtime(runtime)
    session = f"wl-{action.replace('_', '-')}"
    try:
        task_id = await runtime.supervisor.start_task(session, "сделай", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        row = runtime.tasks.get(task_id)
        assert row["status"] == TaskStatus.failed.value
        assert row["error"] == "not_in_whitelist"
        assert actions.calls == []
        assert float(row["spend_actions"] or 0) == 0.0
        assert [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"] == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _FORBIDDEN)
async def test_grant_refuses_forbidden_action(token: str, action: str) -> None:
    del token
    actions = _ClosedRegistry(safe={action})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([]),
    )
    set_runtime(runtime)
    session = f"grant-{action.replace('_', '-')}"
    try:
        task_id = runtime.tasks.insert(session, "цель", TaskStatus.waiting_approval.value, project_id=13)
        runtime.checkpoints.save(
            task_id,
            session,
            CheckpointData(plan=[PlanStep(action=action, risk="high")], position=0),
        )
        runtime.supervisor._session_task[session] = task_id
        runtime.supervisor._block_reason[task_id] = "risk"
        with pytest.raises(ApprovalGrantError) as caught:
            await runtime.supervisor.grant_approval(session)
        assert caught.value.status_code == 403
        assert caught.value.detail == "not_in_whitelist"
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _FORBIDDEN)
async def test_http_forbidden_action_rejected_at_approve_and_run(token: str, action: str) -> None:
    del token
    actions = _ClosedRegistry(safe={action})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=action, risk="none")]),
    )
    set_runtime(runtime)
    session = f"http-{action.replace('_', '-')}"
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            posted = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "сделай", "mode": "task", "project_id": 13},
                headers=_headers(),
            )
            assert posted.status_code == 200
            await runtime.queue.drain()
            task_id = runtime.supervisor.active_task(session)
            assert task_id is not None
            await runtime.supervisor.wait(task_id, timeout=5)
            row = runtime.tasks.get(task_id)
            assert row["error"] == "not_in_whitelist"
            assert actions.calls == []
            refused = await client.post(
                f"/api/orchestrator/chat/{session}/approve",
                json={"decision": "approve"},
                headers=_headers(),
            )
            assert refused.status_code == 409
            assert actions.calls == []

            grant_session = f"http-grant-{action.replace('_', '-')}"
            held = runtime.tasks.insert(
                grant_session,
                "цель",
                TaskStatus.waiting_approval.value,
                project_id=13,
            )
            runtime.checkpoints.save(
                held,
                grant_session,
                CheckpointData(plan=[PlanStep(action=action, risk="high")], position=0),
            )
            runtime.supervisor._session_task[grant_session] = held
            runtime.supervisor._block_reason[held] = "risk"
            denied = await client.post(
                f"/api/orchestrator/chat/{grant_session}/approve",
                json={"decision": "approve"},
                headers=_headers(),
            )
            assert denied.status_code == 403
            assert denied.json()["detail"] == "not_in_whitelist"
            assert actions.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_execute_refuses_denylist_after_risk_grant(token: str) -> None:
    del token
    actions = _ClosedRegistry(safe={"exec"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([]),
    )
    set_runtime(runtime)
    session = "wl-execute"
    try:
        task_id = runtime.tasks.insert(session, "цель", TaskStatus.executing.value, project_id=13)
        step = PlanStep(action="exec", risk="high")
        runtime.supervisor._risk_granted.add(runtime.supervisor._risk_grant_key(task_id, 0, step))
        await runtime.supervisor._execute_plan(task_id, session, [step], 0, "цель")
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["error"] == "not_in_whitelist"
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_known_look_still_runs(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look", risk="none")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("wl-look", "смотри")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["look"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)
