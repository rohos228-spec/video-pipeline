"""studio_dry_run + HTTP query dry_run."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow
from app.web.api import create_app
from app.web.deps import get_session
from app.web.studio_dry_run import (
    FORBIDDEN_DRY_RUN_STEPS,
    validate_project_step_dry_run,
)

app = create_app()


@pytest_asyncio.fixture
async def api_client(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dry_run.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen

    async with factory() as session:
        session.add(
            Workflow(
                name="default",
                is_default=True,
                nodes=[],
                edges=[],
            )
        )
        p = Project(topic="dry", slug="dry-run-test", status=ProjectStatus.new)
        session.add(p)
        await session.commit()
        await session.refresh(p)
        project_id = p.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, project_id, factory

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_validate_plan_ok(api_client) -> None:
    client, project_id, factory = api_client
    async with factory() as session:
        p = await session.get(Project, project_id)
        out = await validate_project_step_dry_run(session, p, "plan")
    assert out["ok"] is True
    assert out["would_status"] == ProjectStatus.planning.value

    r = await client.post(f"/api/projects/{project_id}/steps/plan/run?dry_run=true")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_video_dry_run_forbidden(api_client) -> None:
    client, project_id, _factory = api_client
    assert "video" in FORBIDDEN_DRY_RUN_STEPS
    r = await client.post(f"/api/projects/{project_id}/steps/video/run?dry_run=true")
    assert r.status_code == 400
    assert "dry_run" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dry_run_does_not_change_status(api_client) -> None:
    client, project_id, factory = api_client
    async with factory() as session:
        p = await session.get(Project, project_id)
        before = p.status

    await client.post(f"/api/projects/{project_id}/steps/plan/run?dry_run=true")

    async with factory() as session:
        p = await session.get(Project, project_id)
        assert p.status == before
