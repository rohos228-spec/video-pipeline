"""GET /api/projects не должен звать recompute_status (сайдбар должен быть быстрым)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'list.db'}"
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
async def test_list_projects_skips_recompute(client, monkeypatch) -> None:
    ac, factory = client
    calls: list[str] = []

    async def _boom(*_a, **_k):
        calls.append("recompute")
        raise AssertionError("recompute_status must not run on list")

    monkeypatch.setattr("app.web.routers.projects.recompute_status", _boom)

    async with factory() as session:
        session.add(
            Project(
                slug="list-fast",
                topic="T",
                status=ProjectStatus.new,
                hero_mode="no_hero",
                auto_mode=True,
            )
        )
        await session.commit()

    res = await ac.get("/api/projects")
    assert res.status_code == 200
    assert calls == []
    rows = res.json()
    assert any(r["slug"] == "list-fast" for r in rows)
