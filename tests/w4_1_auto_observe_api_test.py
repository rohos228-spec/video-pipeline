"""W4-1 / C01: маршрут GET/POST /projects/{id}/observations/auto (состояние и выключатель автонаблюдения). Только tmp."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, WorkflowRun
from app.services import observation
from app.services.access_guard import LocalOnlyDep
from app.web.auth_sessions import require_web_user
from app.web.routers import observations


@pytest.fixture
async def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSERVER_AUTO_ENABLE", "1")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'master.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await observation.migrate_observation_schema(conn)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    observer = observation.Observer(maker, body_root=tmp_path / "bodies")
    monkeypatch.setattr(observation, "_observer", observer)
    async with maker() as s:
        project = Project(slug="api-auto", topic="t", status=ProjectStatus.planning, meta={})
        s.add(project)
        await s.flush()
        s.add(WorkflowRun(workflow_id=1, project_id=project.id,
                          nodes_snapshot=[{"id": "n1", "type": "plan"}], edges_snapshot=[]))
        await s.commit()
        pid = project.id
    app = FastAPI()
    app.dependency_overrides[require_web_user] = lambda: None
    app.dependency_overrides[LocalOnlyDep.dependency] = lambda: None
    app.include_router(observations.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        yield client, observer, maker, pid
    await engine.dispose()


async def test_state_then_switch_off_stops_the_observe_scope_and_blocks_new_one(api):
    client, observer, maker, pid = api
    state = (await client.get(f"/api/projects/{pid}/observations/auto")).json()
    assert state["enabled"] is True and state["project_setting"] is None and state["active_scopes"] == []

    async with maker() as s:
        project = await s.get(Project, pid)
    scope = await observer.ensure_auto_scope(project)
    assert scope is not None
    assert len((await client.get(f"/api/projects/{pid}/observations/auto")).json()["active_scopes"]) == 1

    off = (await client.post(f"/api/projects/{pid}/observations/auto", json={"enabled": False})).json()
    assert off["enabled"] is False and off["project_setting"] is False
    assert off["active_scopes"] == []
    async with maker() as s:
        project = await s.get(Project, pid)
        assert project.meta["observer_auto"] is False
    assert await observer.ensure_auto_scope(project) is None

    on = (await client.post(f"/api/projects/{pid}/observations/auto", json={"enabled": True})).json()
    assert on["enabled"] is True and on["project_setting"] is True
    assert len(on["active_scopes"]) == 1  # включили обратно: новая область observe


async def test_unknown_project_is_404(api):
    client, *_ = api
    assert (await client.get("/api/projects/99999/observations/auto")).status_code == 404
    assert (await client.post("/api/projects/99999/observations/auto", json={"enabled": False})).status_code == 404
