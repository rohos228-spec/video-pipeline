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
from app.services.batches import (
    migrate_legacy_batch_paused_flags,
    pause_all_running_batches,
    pause_batch_queue,
    resume_all_paused_batches,
    resume_batch_queue,
    start_batch_queue,
)


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


# ---------------------------------------------------------------------------
# Regression tests for bugs NOT covered by the initial PR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_batch_pause_then_global_resume_re_enables_subs(session):
    """Bug #1: per-batch pause must stamp _batch_paused so that the global
    resume (resume_all_paused_batches) can restore auto_mode.

    Without the fix, pause_batch_queue sets auto_mode=False but no flag, so
    resume_all_paused_batches skips every sub-project and the batch silently
    stays stuck even though its status flips to 'running'.
    """
    b = await _mk_batch(session, status=BatchStatus.running)
    p = await _mk_sub(session, b, status=ProjectStatus.hero_ready, auto_mode=True)

    # User presses the per-batch Pause button
    await pause_batch_queue(session, b.id)
    await session.refresh(p)
    assert not p.auto_mode, "per-batch pause must disable auto_mode"
    assert (p.meta or {}).get("_batch_paused"), (
        "pause_batch_queue must stamp _batch_paused so global resume can find it"
    )

    # User presses the global Resume button
    await resume_all_paused_batches(session)
    await session.refresh(p)
    assert p.auto_mode, (
        "global resume must re-enable a project paused by per-batch pause"
    )
    assert not (p.meta or {}).get("_batch_paused"), "_batch_paused flag must be cleared"


@pytest.mark.asyncio
async def test_stale_batch_paused_flag_does_not_override_user_stop(session):
    """Bug #2: resume_batch_queue must clear the _batch_paused flag it finds,
    otherwise a subsequent manual user-stop leaves a stale flag that causes
    the next global resume to silently re-enable the stopped project.

    Trigger sequence:
      global pause  →  per-batch resume  →  user manual stop  →  global resume
    """
    b = await _mk_batch(session, status=BatchStatus.running)
    p = await _mk_sub(session, b, status=ProjectStatus.hero_ready, auto_mode=True)

    # Step 1: global pause stamps _batch_paused=True
    await pause_all_running_batches(session)
    await session.refresh(p)
    assert (p.meta or {}).get("_batch_paused"), "precondition: flag stamped"

    # Step 2: per-batch resume re-enables auto_mode but must ALSO clear the flag
    await session.refresh(b)
    await resume_batch_queue(session, b.id)
    await session.refresh(p)
    assert p.auto_mode, "per-batch resume must re-enable auto_mode"
    assert not (p.meta or {}).get("_batch_paused"), (
        "resume_batch_queue must clear _batch_paused to prevent stale-flag bug"
    )

    # Step 3: user manually stops the project (simulate on_project_stop_running)
    p.auto_mode = False
    await session.flush()

    # Step 4: another global pause + global resume cycle
    await pause_all_running_batches(session)
    await resume_all_paused_batches(session)
    await session.refresh(p)

    assert not p.auto_mode, (
        "User-stopped project MUST remain stopped after global pause/resume — "
        "stale _batch_paused flag must not re-enable it"
    )


@pytest.mark.asyncio
async def test_per_batch_pause_resume_preserves_user_stopped_project(session):
    """per-batch pause → per-batch resume must not re-enable a user-stopped
    sub-project (auto_mode=False without _batch_paused flag)."""
    b = await _mk_batch(session, status=BatchStatus.running)
    # User-stopped project (no _batch_paused flag)
    p_stopped = await _mk_sub(
        session, b, status=ProjectStatus.plan_ready, auto_mode=False
    )
    # Normally-running project
    p_running = await _mk_sub(
        session, b, status=ProjectStatus.hero_ready, auto_mode=True
    )

    await pause_batch_queue(session, b.id)
    await session.refresh(p_stopped)
    await session.refresh(p_running)

    assert not p_stopped.auto_mode
    assert not (p_stopped.meta or {}).get("_batch_paused"), (
        "user-stopped project must not receive _batch_paused"
    )
    assert not p_running.auto_mode
    assert (p_running.meta or {}).get("_batch_paused"), (
        "normally-running project must receive _batch_paused"
    )

    await resume_batch_queue(session, b.id)
    await session.refresh(p_stopped)
    await session.refresh(p_running)

    assert not p_stopped.auto_mode, (
        "User-stopped project MUST remain auto_mode=False after per-batch resume"
    )
    assert p_running.auto_mode, (
        "Batch-paused project MUST be re-enabled after per-batch resume"
    )


@pytest.mark.asyncio
async def test_start_batch_queue_clears_stale_batch_paused_flags(session):
    """start_batch_queue must clear any stale _batch_paused flags so that a
    subsequent global resume cannot incorrectly re-enable a project that the
    queue start intentionally set to auto_mode=False."""
    b = await _mk_batch(session, status=BatchStatus.running)
    # Plant a stale _batch_paused flag (as if a previous global pause left it)
    p = await _mk_sub(
        session, b,
        status=ProjectStatus.new,
        auto_mode=True,
        meta={"_batch_paused": True},
    )

    await start_batch_queue(session, b.id)
    await session.refresh(p)

    # Flag must be gone regardless of whether auto_mode ended up True or False
    assert not (p.meta or {}).get("_batch_paused"), (
        "start_batch_queue must clear stale _batch_paused flags"
    )


# ---------------------------------------------------------------------------
# Migration tests for legacy databases (paused before _batch_paused existed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_legacy_batch_paused_flags_stamps_flag(session):
    """migrate_legacy_batch_paused_flags must stamp _batch_paused=True on
    sub-projects of paused batches that have auto_mode=False but no flag.

    This covers databases produced by the old code where pause_batch_queue
    set auto_mode=False without stamping the flag.
    """
    b = await _mk_batch(session, status=BatchStatus.paused)
    # Legacy state: auto_mode=False, no _batch_paused flag
    p = await _mk_sub(session, b, status=ProjectStatus.hero_ready, auto_mode=False)

    count = await migrate_legacy_batch_paused_flags(session)
    await session.refresh(p)

    assert count == 1, "migration must stamp 1 sub-project"
    assert (p.meta or {}).get("_batch_paused"), (
        "migration must stamp _batch_paused=True on legacy paused sub"
    )


@pytest.mark.asyncio
async def test_migrate_then_resume_re_enables_legacy_paused_batch(session):
    """End-to-end: after migration, resume_batch_queue must re-enable a
    sub-project that was paused with the old code (no _batch_paused flag).

    Regression for the silent-stuck-batch bug: without the migration,
    batch.status flips to 'running' but no sub-project gets auto_mode=True
    and the batch silently produces nothing.
    """
    b = await _mk_batch(session, status=BatchStatus.paused)
    p = await _mk_sub(session, b, status=ProjectStatus.hero_ready, auto_mode=False)

    # Run migration first (simulates what _migrate_batch_paused_flags does
    # at app startup)
    await migrate_legacy_batch_paused_flags(session)
    await session.refresh(p)
    assert (p.meta or {}).get("_batch_paused"), "precondition: flag stamped by migration"

    # Now user presses Resume — should work correctly
    await resume_batch_queue(session, b.id)
    await session.refresh(p)
    await session.refresh(b)

    assert b.status is BatchStatus.running, "batch must be running after resume"
    assert p.auto_mode, (
        "legacy-paused sub must have auto_mode=True after migration + resume"
    )
    assert not (p.meta or {}).get("_batch_paused"), "_batch_paused flag must be cleared"


@pytest.mark.asyncio
async def test_migrate_is_idempotent(session):
    """Running migrate_legacy_batch_paused_flags twice must not double-stamp
    or corrupt any flags."""
    b = await _mk_batch(session, status=BatchStatus.paused)
    p = await _mk_sub(session, b, status=ProjectStatus.new, auto_mode=False)

    n1 = await migrate_legacy_batch_paused_flags(session)
    n2 = await migrate_legacy_batch_paused_flags(session)

    assert n1 == 1, "first run stamps 1 sub"
    assert n2 == 0, "second run is a no-op (already stamped)"
    await session.refresh(p)
    assert (p.meta or {}).get("_batch_paused"), "flag must still be present"


@pytest.mark.asyncio
async def test_migrate_skips_running_batches(session):
    """migrate_legacy_batch_paused_flags must only process PAUSED batches,
    not running ones — to avoid interfering with live batches."""
    b_running = await _mk_batch(session, status=BatchStatus.running)
    p = await _mk_sub(session, b_running, status=ProjectStatus.hero_ready, auto_mode=False)

    count = await migrate_legacy_batch_paused_flags(session)
    await session.refresh(p)

    assert count == 0, "running-batch subs must NOT be stamped"
    assert not (p.meta or {}).get("_batch_paused"), (
        "sub in running batch must not receive _batch_paused"
    )


@pytest.mark.asyncio
async def test_migrate_skips_terminal_status_projects(session):
    """migrate_legacy_batch_paused_flags must not stamp published/failed/paused
    sub-projects — those are not eligible for resume anyway."""
    b = await _mk_batch(session, status=BatchStatus.paused)
    p_pub = await _mk_sub(
        session, b, status=ProjectStatus.published, auto_mode=False
    )
    p_failed = await _mk_sub(
        session, b, status=ProjectStatus.failed, auto_mode=False
    )

    count = await migrate_legacy_batch_paused_flags(session)
    await session.refresh(p_pub)
    await session.refresh(p_failed)

    assert count == 0, "terminal projects must NOT be stamped"
    assert not (p_pub.meta or {}).get("_batch_paused")
    assert not (p_failed.meta or {}).get("_batch_paused")
