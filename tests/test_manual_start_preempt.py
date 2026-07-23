"""Ручной старт из Studio: preempt running + очистка stale meta."""

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
async def test_explicit_ui_start_preempts_other_running(session, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="preempt",
        topic="t",
        status=ProjectStatus.generating_hero,
        general_plan="x" * 200,
        script_text="script",
        auto_mode=True,
        meta={"user_stop": True, "enrich_completed_slots": [1, 2]},
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)

    status = await start_step(
        session, p, "split", skip_queue_guard=True, explicit_ui_start=True
    )
    assert status is ProjectStatus.splitting
    assert not (p.meta or {}).get("user_stop")
    assert "enrich_completed_slots" not in (p.meta or {})
    assert "split_completed" not in (p.meta or {})
