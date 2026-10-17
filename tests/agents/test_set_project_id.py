"""project_id, уже записанный в задаче, не затирается."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import PlanStep, TaskStatus
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def test_set_project_id_never_overwrites_existing(token: str) -> None:
    del token
    store = TaskStore()
    bound = store.insert("proj-keep", "цель", TaskStatus.queued.value, project_id=65)
    store.set_project_id(bound, 66)
    assert store.get(bound)["project_id"] == 65
    empty = store.insert("proj-fill", "цель", TaskStatus.queued.value)
    store.set_project_id(empty, 65)
    assert store.get(empty)["project_id"] == 65


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


@pytest.mark.asyncio
async def test_project_route_keeps_a_different_binding(token: str) -> None:
    del token
    actions = MockActions(risks={"charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="charge", risk="low", mutating=True, estimated_cost=1)]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("proj-http", "риск", project_id=65)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["project_id"] == 65
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/orchestrator/chat/proj-http/project",
                json={"project_id": 66},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
        assert response.status_code == 409
        assert runtime.tasks.get(task_id)["project_id"] == 65
        assert TEST_TOKEN not in response.text
    finally:
        set_runtime(None)
