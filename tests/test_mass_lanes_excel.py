"""Тесты mass-lanes API: темы из Excel meta."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.web.api import create_app

app = create_app()
from app.models import Base, Project, ProjectStatus
from app.web.deps import get_session


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'mass.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_mass_lanes_uses_excel_topics_from_meta(session_factory, monkeypatch) -> None:
    async def _no_wf() -> None:
        return None

    monkeypatch.setattr(
        "app.services.mass_factory._get_default_workflow_id",
        _no_wf,
    )
    async with session_factory() as session:
        parent = Project(
            slug="parent-excel",
            topic="Шаблон",
            status=ProjectStatus.new,
            auto_mode=False,
            meta={
                "mass_excel_topics": ["Тема A", "Тема B", "Тема C"],
                "ai_control": True,
            },
        )
        session.add(parent)
        await session.commit()
        await session.refresh(parent)
        parent_id = parent.id

    async def override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/api/projects/{parent_id}/mass-lanes/start",
                json={},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["count"] == 1
            assert data["queue_size"] == 3
            assert data["started_id"] is not None
            topics_started = [c["topic"] for c in data["created"]]
            assert topics_started == ["Тема A"]
    finally:
        app.dependency_overrides.pop(get_session, None)
