"""Tests for batch pause/resume: user-stopped sub-projects must NOT be
re-enabled by a global batch resume.

Reproduces the bug introduced in 5249ae0 (⏹ Остановить теперь реально
прерывает шаг): `on_project_stop_running` sets `project.auto_mode=False`
but `resume_all_paused_batches` was re-enabling ALL `auto_mode=False`
projects, silently undoing the user's explicit stop.

Fix: `pause_all_running_batches` now stamps a `_batch_paused=True` flag
in `project.meta` for every project whose `auto_mode` it disables.
`resume_all_paused_batches` only re-enables projects that carry this flag,
leaving user-stopped projects alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, BatchProject, BatchStatus, Project, ProjectStatus
from app.services.batches import pause_all_running_batches, resume_all_paused_batches


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    """In-memory SQLite session with all tables."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        yield s
    await engine.dispose()


async def _mk_batch(session, *, status: BatchStatus = BatchStatus.running) -> BatchProject:
    b = BatchProject(slug="batch1", name="Batch 1", status=status)
    session.add(b)
    await session.flush()
    return b


async def _mk_sub(
    session,
    batch: BatchProject,
    *,
    status: ProjectStatus = ProjectStatus.new,
    auto_mode: bool = True,
    meta: dict | None = None,
) -> Project:
    p = Project(
        slug=f"sub_{status.value}",
        topic="t",
        hero_mode="full_auto",
        status=status,
        auto_mode=auto_mode,
        batch_id=batch.id,
    )
    if meta:
        p.meta = meta
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_pause_resume_cycle_normal(session):
    """Normal flow: running batch → pause → resume restores auto_mode."""
    b = await _mk_batch(session, status=BatchStatus.running)
    # Sub-project with auto_mode=True in a ready status
    p = await _mk_sub(session, b, status=ProjectStatus.hero_ready, auto_mode=True)

    await pause_all_running_batches(session)
    await session.refresh(p)
    assert not p.auto_mode, "pause should disable auto_mode"
    assert (p.meta or {}).get("_batch_paused"), "pause must stamp _batch_paused flag"

    # Also refresh batch
    await session.refresh(b)
    assert b.status is BatchStatus.paused

    await resume_all_paused_batches(session)
    await session.refresh(p)
    assert p.auto_mode, "resume should re-enable a batch-paused project"
    assert not (p.meta or {}).get("_batch_paused"), "_batch_paused flag must be cleared"


@pytest.mark.asyncio
async def test_user_stopped_project_survives_pause_resume(session):
    """Bug regression: user-stopped sub-project must NOT be re-enabled by
    a global batch resume, even if a batch pause/resume cycle happens."""
    b = await _mk_batch(session, status=BatchStatus.running)
    # Simulate user pressing "⏹ Остановить": auto_mode=False, no _batch_paused flag
    p_stopped = await _mk_sub(
        session, b, status=ProjectStatus.hero_ready, auto_mode=False
    )
    # Another sub-project running normally (auto_mode=True)
    p_running = await _mk_sub(
        session, b, status=ProjectStatus.images_ready, auto_mode=True
    )

    # Batch pause — the normally-running project gets _batch_paused, the
    # user-stopped one does NOT (it already had auto_mode=False).
    await pause_all_running_batches(session)
    await session.refresh(p_stopped)
    await session.refresh(p_running)

    assert not p_stopped.auto_mode
    assert not (p_stopped.meta or {}).get("_batch_paused"), (
        "user-stopped project must not receive _batch_paused flag"
    )
    assert not p_running.auto_mode
    assert (p_running.meta or {}).get("_batch_paused"), (
        "normally-running project must receive _batch_paused flag"
    )

    # Batch resume — only the batch-paused project comes back.
    await resume_all_paused_batches(session)
    await session.refresh(p_stopped)
    await session.refresh(p_running)

    assert not p_stopped.auto_mode, (
        "User-stopped project MUST remain auto_mode=False after batch resume"
    )
    assert p_running.auto_mode, (
        "Batch-paused project MUST be re-enabled after batch resume"
    )


@pytest.mark.asyncio
async def test_pause_does_not_affect_new_status_projects_with_auto_mode_false(session):
    """A sub-project already at auto_mode=False (user-stopped) in 'new' status
    must not receive the _batch_paused stamp — it was already stopped."""
    b = await _mk_batch(session, status=BatchStatus.running)
    p = await _mk_sub(session, b, status=ProjectStatus.new, auto_mode=False)

    await pause_all_running_batches(session)
    await session.refresh(p)

    assert not (p.meta or {}).get("_batch_paused"), (
        "Pre-stopped new project must not get _batch_paused"
    )
