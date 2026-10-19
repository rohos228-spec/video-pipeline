"""Привязка проекта не снимает бюджетный стоп."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import ApprovalEvent, ApprovalReason, LLMResponse, PlanStep, TaskStatus
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _budget() -> LLMResponse:
    return LLMResponse(
        text="бюджет кончился",
        call_id="budget-call",
        model="scripted",
        provider="52pay",
        tier="strong",
        finish_reason="stop",
        task_status=TaskStatus.waiting_approval,
        approval=ApprovalEvent(reason="budget_exhausted", payload={"spent_usd": 10}),
    )


@pytest.mark.asyncio
async def test_project_bind_keeps_budget_stop(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="edit", mutating=True)]),
    )
    set_runtime(runtime)
    session = "bind-budget"
    try:
        task_id = await runtime.supervisor.start_task(session, "без проекта")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.supervisor._block_reason.get(task_id) == ApprovalReason.missing_project_id.value
        await runtime.supervisor.absorb_budget(task_id, session, _budget())
        assert task_id in runtime.supervisor._budget_stop
        assert runtime.supervisor._block_reason.get(task_id) == ApprovalReason.missing_project_id.value
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bound = await client.post(
                f"/api/orchestrator/chat/{session}/project",
                json={"project_id": 13},
                headers=_headers(),
            )
        assert bound.status_code == 409
        assert bound.json()["detail"] == "budget_exhausted"
        assert actions.calls == []
        assert task_id in runtime.supervisor._budget_stop
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
