"""HTTP-роуты GPT workspace: save не падает 500, вложения, ошибки."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, Workflow
from app.services import gpt_workspace as gw
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gw.settings, "data_dir", tmp_path)
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'gw.db'}"
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
        session.add(
            Project(slug="gw-proj", title="GW", topic="", status="new", hero_mode="no_hero")
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory, tmp_path

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_upload_empty_and_missing_session(client) -> None:
    c, _, _ = client
    s = (await c.post("/api/gpt-workspace/sessions", json={"title": "t"})).json()
    sid = s["id"]

    r = await c.post(
        f"/api/gpt-workspace/sessions/{sid}/attachments",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert r.status_code == 400
    assert "пустой" in r.json()["detail"]

    r = await c.post(
        "/api/gpt-workspace/sessions/nosuch/attachments",
        files={"file": ("a.txt", b"hi", "text/plain")},
    )
    assert r.status_code == 404
    assert "сессия не найдена" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_and_list_attachment(client) -> None:
    c, _, _ = client
    s = (await c.post("/api/gpt-workspace/sessions", json={})).json()
    sid = s["id"]
    r = await c.post(
        f"/api/gpt-workspace/sessions/{sid}/attachments",
        files={"file": ("note.txt", b"hello-attach", "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "note.txt"
    got = (await c.get(f"/api/gpt-workspace/sessions/{sid}")).json()
    assert [a["name"] for a in got["attachments"]] == ["note.txt"]


@pytest.mark.asyncio
async def test_save_voiceover_and_missing_project(client) -> None:
    from sqlalchemy import select

    c, factory, _ = client
    s = gw.create_session()
    gw._append_message(s["id"], "assistant", "текст озвучки")

    r = await c.post(
        f"/api/gpt-workspace/sessions/{s['id']}/save-voiceover",
        json={"project_id": 999999},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "project not found"

    async with factory() as db:
        proj = (await db.execute(select(Project))).scalars().first()
        assert proj is not None
        pid = proj.id

    r = await c.post(
        f"/api/gpt-workspace/sessions/{s['id']}/save-voiceover",
        json={"project_id": pid},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "voiceover.txt"


@pytest.mark.asyncio
async def test_save_output_missing_file(client) -> None:
    c, factory, _ = client
    s = gw.create_session()
    async with factory() as db:
        from sqlalchemy import select

        proj = (await db.execute(select(Project))).scalars().first()
        assert proj is not None
        pid = proj.id

    r = await c.post(
        f"/api/gpt-workspace/sessions/{s['id']}/save-to-project",
        json={"project_id": pid, "output_name": "nope.txt"},
    )
    assert r.status_code == 404
    assert "нет файла" in r.json()["detail"]


@pytest.mark.asyncio
async def test_ask_missing_session(client) -> None:
    c, _, _ = client
    r = await c.post(
        "/api/gpt-workspace/sessions/zzz/ask",
        json={"message": "hi"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ask_api_unavailable_returns_503(client, monkeypatch) -> None:
    c, _, _ = client
    from app.services.gpt_client import GptApiUnavailable
    import app.services.gpt_client as gc

    class Boom:
        async def ask_with_files(self, *a, **k):
            raise GptApiUnavailable("GPT API не настроен: test")

    monkeypatch.setattr(gc, "get_gpt_client", lambda: Boom())
    s = (await c.post("/api/gpt-workspace/sessions", json={})).json()
    r = await c.post(
        f"/api/gpt-workspace/sessions/{s['id']}/ask",
        json={"message": "привет"},
    )
    assert r.status_code == 503
    assert "не настроен" in r.json()["detail"]
