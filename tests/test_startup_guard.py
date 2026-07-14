from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, BatchProject, BatchStatus, Project, ProjectStatus
from app.services.mass_pause import is_active as mass_pause_active
from app.services.startup_guard import block_pipeline_autorun_on_startup


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_guard_blocks_all_automatic_continuation(
    session: AsyncSession,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    running_project = Project(
        slug="running",
        topic="Running",
        status=ProjectStatus.scripting,
        auto_mode=True,
    )
    auto_ready_project = Project(
        slug="auto-ready",
        topic="Auto ready",
        status=ProjectStatus.plan_ready,
        auto_mode=True,
    )
    batch = BatchProject(name="Batch", slug="batch", status=BatchStatus.running)
    session.add_all([running_project, auto_ready_project, batch])
    await session.flush()

    stats = await block_pipeline_autorun_on_startup(session)

    assert stats["running_projects_rolled_back"] == 1
    assert stats["auto_mode_disabled"] == 2
    assert stats["user_stop_gates_set"] == 2
    assert stats["batches_paused"] == 1
    assert stats["mass_pause_enabled"] is True
    assert running_project.status is ProjectStatus.plan_ready
    assert running_project.auto_mode is False
    assert (running_project.meta or {}).get("user_stop") is True
    assert auto_ready_project.status is ProjectStatus.plan_ready
    assert auto_ready_project.auto_mode is False
    assert (auto_ready_project.meta or {}).get("user_stop") is True
    assert batch.status is BatchStatus.paused
    assert mass_pause_active() is True
