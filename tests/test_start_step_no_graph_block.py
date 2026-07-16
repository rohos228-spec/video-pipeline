"""Изолированная нода без рёбер: linear prereq всё ещё разрешает in-order запуск."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.project_steps import start_step


@pytest.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_start_script_without_canvas_edges(session, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="solo-script",
        topic="t",
        status=ProjectStatus.plan_ready,
        general_plan="x" * 200,
        meta={
            "canvas_graph": {
                "nodes": [{"id": "n_script", "type": "script", "position": {"x": 0, "y": 0}, "data": {}}],
                "edges": [],
            }
        },
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)

    status = await start_step(session, p, "script", skip_queue_guard=True)
    assert status is ProjectStatus.scripting
