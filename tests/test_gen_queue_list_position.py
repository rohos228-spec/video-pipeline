"""GET /projects должен отдавать gen_queue_position и для дочерних проектов."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services import sidebar_layout as layout_svc
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'q.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_projects_queue_position_for_child(client) -> None:
    ac, factory = client
    async with factory() as session:
        parent = Project(
            slug="parent-q",
            topic="Parent",
            status=ProjectStatus.new,
            hero_mode="no_hero",
            auto_mode=True,
        )
        session.add(parent)
        await session.flush()
        child = Project(
            slug="child-q",
            topic="Child",
            status=ProjectStatus.new,
            hero_mode="no_hero",
            auto_mode=True,
            meta={"mass_parent_id": parent.id, "project_child_manual": True},
        )
        session.add(child)
        await session.commit()
        await session.refresh(child)
        child_id = child.id

    layout_svc.set_gen_queue([child_id])

    res = await ac.get("/api/projects")
    assert res.status_code == 200
    rows = res.json()
    by_id = {r["id"]: r for r in rows}
    assert child_id in by_id
    assert by_id[child_id]["gen_queue_position"] == 1
