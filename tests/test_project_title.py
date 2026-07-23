"""Название проекта (title) отделено от topic ноды «Тема ролика»."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, Workflow
from app.services.project_display import project_display_name
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()


@pytest_asyncio.fixture
async def client(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'title.db'}"
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
                nodes=[{"id": "plan_1", "type": "plan", "x": 0, "y": 0}],
                edges=[],
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_project_sets_title_not_topic(client) -> None:
    c, _ = client
    r = await c.post("/api/projects", json={"title": "Мой ролик", "hero_mode": "auto"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Мой ролик"
    assert body["topic"] == ""


@pytest.mark.asyncio
async def test_patch_project_title(client) -> None:
    c, factory = client
    r = await c.post("/api/projects", json={"title": "Старое имя"})
    pid = r.json()["id"]
    r2 = await c.patch(f"/api/projects/{pid}", json={"title": "Новое имя"})
    assert r2.status_code == 200
    assert r2.json()["title"] == "Новое имя"
    assert r2.json()["topic"] == ""

    async with factory() as session:
        p = await session.get(Project, pid)
        assert p is not None
        assert p.title == "Новое имя"
        assert p.topic == ""


def test_project_display_name_prefers_title() -> None:
    p = Project(slug="slug-only", topic="Тема пайплайна", title="Название в сайдбаре")
    assert project_display_name(p) == "Название в сайдбаре"


def test_project_display_name_legacy_topic_fallback() -> None:
    p = Project(slug="legacy-slug", topic="Старая тема", title=None)
    assert project_display_name(p) == "Старая тема"
