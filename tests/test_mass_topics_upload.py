"""Загрузка Excel в фабрику: первая и повторная."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'mass_upload.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    yield factory
    await engine.dispose()


def _write_topics_xlsx(path: Path, topics: list[str]) -> None:
    wb = Workbook()
    ws = wb.active
    for topic in topics:
        ws.append([topic])
    wb.save(path)


@pytest.mark.asyncio
async def test_parse_topics_first_and_second_upload(session_factory, tmp_path: Path) -> None:
    async with session_factory() as session:
        session.add(
            Workflow(
                name="default",
                is_default=True,
                nodes=[{"id": "n_plan", "type": "plan"}],
                edges=[],
            )
        )
        parent = Project(
            slug="factory-parent",
            topic="Шаблон",
            status=ProjectStatus.new,
            hero_mode="auto",
            meta={},
        )
        session.add(parent)
        await session.commit()
        await session.refresh(parent)
        parent_id = parent.id

    xlsx1 = tmp_path / "topics1.xlsx"
    xlsx2 = tmp_path / "topics2.xlsx"
    _write_topics_xlsx(xlsx1, ["Тема A", "Тема B"])
    _write_topics_xlsx(xlsx2, ["Тема X", "Тема Y", "Тема Z"])

    async def override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with xlsx1.open("rb") as f:
                r1 = await client.post(
                    f"/api/projects/{parent_id}/mass-lanes/parse-topics",
                    files={
                        "file": (
                            "topics1.xlsx",
                            f,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    },
                )
            assert r1.status_code == 200, r1.text
            data1 = r1.json()
            assert data1["count"] == 2
            assert data1["revision"] == 1
            assert data1["topics"] == ["Тема A", "Тема B"]

            with xlsx2.open("rb") as f:
                r2 = await client.post(
                    f"/api/projects/{parent_id}/mass-lanes/parse-topics",
                    files={
                        "file": (
                            "topics2.xlsx",
                            f,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    },
                )
            assert r2.status_code == 200, r2.text
            data2 = r2.json()
            assert data2["count"] == 3
            assert data2["revision"] == 2
            assert data2["topics"] == ["Тема X", "Тема Y", "Тема Z"]
            assert data2["queued_after_current"] is False
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_parse_topics_reupload_while_child_busy(session_factory, tmp_path: Path) -> None:
    async with session_factory() as session:
        session.add(
            Workflow(
                name="default",
                is_default=True,
                nodes=[{"id": "n_plan", "type": "plan"}],
                edges=[],
            )
        )
        parent = Project(
            slug="factory-busy",
            topic="Шаблон",
            status=ProjectStatus.new,
            hero_mode="auto",
            meta={
                "mass_factory": True,
                "mass_excel_topics": ["old"],
                "mass_queue_topics": ["old"],
                "mass_excel_revision": 1,
            },
        )
        session.add(parent)
        await session.flush()
        parent_id = parent.id
        session.add(
            Project(
                slug="child-busy",
                topic="running",
                status=ProjectStatus.plan_ready,
                hero_mode="auto",
                meta={"mass_parent_id": parent_id, "mass_lane_position": 1},
            )
        )
        await session.commit()

    xlsx = tmp_path / "topics.xlsx"
    _write_topics_xlsx(xlsx, ["Новая 1", "Новая 2"])

    async def override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with xlsx.open("rb") as f:
                r = await client.post(
                    f"/api/projects/{parent_id}/mass-lanes/parse-topics",
                    files={
                        "file": (
                            "topics.xlsx",
                            f,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    },
                )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["queued_after_current"] is True
            assert data["busy_child_id"] is not None
            assert data["topics"] == ["Новая 1", "Новая 2"]
    finally:
        app.dependency_overrides.pop(get_session, None)
