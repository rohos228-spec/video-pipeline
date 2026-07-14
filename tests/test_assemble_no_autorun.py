"""Assemble must not start automatically after restart or via auto_advance."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.fleet.montage_queue import META_ENQUEUED, process_montage_queue
from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import maybe_auto_advance
from app.services.project_control import stop_project_running
from app.services.startup_guard import block_pipeline_autorun_on_startup
from app.services.step_cancel import is_stop_requested


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
async def test_startup_guard_blocks_music_ready_auto_assemble(
    session: AsyncSession,
) -> None:
    p = Project(
        slug="m",
        topic="Montage",
        status=ProjectStatus.music_ready,
        auto_mode=True,
        meta={META_ENQUEUED: True, "montage_queue_at": "2026-01-01T00:00:00"},
    )
    session.add(p)
    await session.flush()

    stats = await block_pipeline_autorun_on_startup(session)

    assert stats["auto_mode_disabled"] == 1
    assert stats["user_stop_gates_set"] == 1
    assert stats["montage_queue_cleared"] == 1
    assert p.auto_mode is False
    assert (p.meta or {}).get("user_stop") is True
    assert (p.meta or {}).get(META_ENQUEUED) is None


@pytest.mark.asyncio
async def test_maybe_auto_advance_does_not_enter_assembling(
    session: AsyncSession,
) -> None:
    p = Project(
        slug="a",
        topic="Assemble",
        status=ProjectStatus.music_ready,
        auto_mode=True,
    )
    session.add(p)
    await session.flush()

    advanced = await maybe_auto_advance(session, p, bot=None)

    assert advanced is False
    assert p.status is ProjectStatus.music_ready


@pytest.mark.asyncio
async def test_montage_queue_skips_user_stop(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.fleet.montage_queue.settings.fleet_montage_hub", True)
    p = Project(
        slug="q",
        topic="Queue",
        status=ProjectStatus.music_ready,
        auto_mode=False,
        meta={META_ENQUEUED: True, "user_stop": True},
    )
    session.add(p)
    await session.flush()

    started = await process_montage_queue(session)

    assert started == 0
    assert p.status is ProjectStatus.music_ready


@pytest.mark.asyncio
async def test_stop_running_keeps_stop_flag(
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path)
    p = Project(
        slug="s",
        topic="Stop",
        status=ProjectStatus.assembling,
        auto_mode=True,
    )
    session.add(p)
    await session.flush()

    await stop_project_running(session, p)

    assert is_stop_requested(p.id)
    assert (p.meta or {}).get("user_stop") is True
    assert p.status is ProjectStatus.audio_ready
