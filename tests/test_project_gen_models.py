"""PATCH проекта: модели картинок/видео + clamp разрешения."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, Workflow
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()


@pytest_asyncio.fixture
async def client(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'gen_models.db'}"
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
async def test_patch_image_and_video_generator(client) -> None:
    c, factory = client
    r = await c.post("/api/projects", json={"title": "модели"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    r2 = await c.patch(
        f"/api/projects/{pid}",
        json={"image_generator": "nano_banana_2", "video_generator": "seedance_2"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["image_generator"] == "nano_banana_2"
    assert body["video_generator"] == "seedance_2"

    async with factory() as session:
        p = await session.get(Project, pid)
        assert p is not None
        assert p.image_generator == "nano_banana_2"
        assert p.video_generator == "seedance_2"


@pytest.mark.asyncio
async def test_patch_image_generator_clamps_resolution(client) -> None:
    c, _ = client
    r = await c.post("/api/projects", json={"title": "clamp"})
    pid = r.json()["id"]
    await c.patch(
        f"/api/projects/{pid}",
        json={"image_generator": "nano_banana_2", "image_resolution": "4k"},
    )
    r2 = await c.patch(
        f"/api/projects/{pid}",
        json={"image_generator": "gpt_image_2"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["image_generator"] == "gpt_image_2"
    assert r2.json()["image_resolution"] == "1k"


@pytest.mark.asyncio
async def test_patch_unknown_generators_rejected(client) -> None:
    c, factory = client
    r = await c.post("/api/projects", json={"title": "bad-model"})
    pid = r.json()["id"]
    r2 = await c.patch(f"/api/projects/{pid}", json={"image_generator": "not_a_model"})
    assert r2.status_code == 400
    r3 = await c.patch(f"/api/projects/{pid}", json={"video_generator": "not_a_model"})
    assert r3.status_code == 400

    async with factory() as session:
        p = await session.get(Project, pid)
        assert p is not None
        assert p.image_generator != "not_a_model"
        assert p.video_generator != "not_a_model"
