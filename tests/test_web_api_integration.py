"""Интеграционные тесты Studio API (ASGI, без живого :8765)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus, Workflow
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()

FORBIDDEN_DRY = {"hero", "items", "img", "video", "audio"}


@pytest_asyncio.fixture
async def client(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'web_api.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen

    async with factory() as session:
        wf = Workflow(
            name="default",
            is_default=True,
            nodes=[{"id": "plan_1", "type": "plan", "x": 0, "y": 0}],
            edges=[],
        )
        session.add(wf)
        p = Project(
            topic="API test",
            slug="api-test-proj",
            status=ProjectStatus.frames_ready,
            hero_mode="no_hero",
        )
        session.add(p)
        await session.flush()
        session.add(
            Frame(
                project_id=p.id,
                number=1,
                voiceover_text="v",
                image_prompt="ip",
                animation_prompt="ap",
                status=FrameStatus.image_generated,
            )
        )
        await session.commit()
        pid = p.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, pid, factory

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_steps_catalog(client) -> None:
    c, _, _ = client
    r = await c.get("/api/projects/steps/catalog")
    assert r.status_code == 200
    codes = {row["code"] for row in r.json()}
    assert "plan" in codes
    assert "video" in codes


@pytest.mark.asyncio
async def test_list_workflows(client) -> None:
    c, _, _ = client
    r = await c.get("/api/workflows")
    assert r.status_code == 200
    assert any(w["is_default"] for w in r.json())


@pytest.mark.asyncio
async def test_get_project_recompute_no_hero(client) -> None:
    """no_hero + frame with prompts → не застревает на frames_ready."""
    c, pid, factory = client
    async with factory() as session:
        from app.models import Artifact, ArtifactKind
        import uuid

        p = await session.get(Project, pid)
        from sqlalchemy import select

        fr = (
            await session.execute(select(Frame).where(Frame.project_id == pid))
        ).scalar_one()
        session.add(
            Artifact(
                project_id=pid,
                frame_id=fr.id,
                kind=ArtifactKind.scene_image,
                uuid=uuid.uuid4().hex,
                path="/tmp/fake.png",
            )
        )
        await session.commit()

    r = await c.get(f"/api/projects/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["hero_mode"] == "no_hero"
    assert body["status"] != "frames_ready"


@pytest.mark.asyncio
async def test_ensure_run_idempotent(client) -> None:
    c, pid, _ = client
    r1 = await c.post(f"/api/projects/{pid}/ensure-run")
    r2 = await c.post(f"/api/projects/{pid}/ensure-run")
    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_dry_run_all_safe_steps(client) -> None:
    c, pid, _ = client
    for code in ("plan", "script", "split", "img_pr", "anim_pr", "assemble"):
        r = await c.post(f"/api/projects/{pid}/steps/{code}/run?dry_run=true")
        assert r.status_code == 200, code


@pytest.mark.asyncio
async def test_dry_run_forbidden_bot_steps(client) -> None:
    c, pid, _ = client
    for code in FORBIDDEN_DRY:
        r = await c.post(f"/api/projects/{pid}/steps/{code}/run?dry_run=true")
        assert r.status_code == 400, code


@pytest.mark.asyncio
async def test_studio_version_endpoint(client) -> None:
    c, _, _ = client
    r = await c.get("/api/studio-version")
    assert r.status_code == 200
    data = r.json()
    assert "build" in data
    assert "ui_stale" in data
