"""SQLite lock при параллельных проектах + счётчик ошибок видео (5 → skip)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.orchestrator.pipeline import advance_project
from app.orchestrator.steps import generate_videos as gv
from app.services.step_global_lock import _SPLIT_LOCK


class _FakeBot:
    async def send_message(self, *a, **kw):
        return None


@pytest.fixture
async def env(tmp_path):
    """Отдельный SQLite + SessionLocal, подменённый на этот engine."""
    db_path = tmp_path / "slk.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def _proj(session: AsyncSession, pid: int) -> Project:
    p = Project(
        id=pid,
        slug=f"p{pid}",
        topic=f"t{pid}",
        status=ProjectStatus.splitting,
        auto_mode=True,
        meta={},
    )
    session.add(p)
    await session.flush()
    return p


async def _proj_status(
    session: AsyncSession, pid: int, status: ProjectStatus
) -> Project:
    p = await _proj(session, pid)
    p.status = status
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_split_serialized_between_projects(env, monkeypatch) -> None:
    """Два проекта в splitting одновременно — lock, не database is locked."""
    engine, factory = env
    order: list[int] = []

    async def fake_run(sess, project, bot=None):
        assert _SPLIT_LOCK.locked()
        order.append(project.id)
        await asyncio.sleep(0.01)
        order.append(-project.id)
        project.status = ProjectStatus.frames_ready
        await sess.flush()

    monkeypatch.setattr("app.orchestrator.steps.split_frames.run", fake_run)
    try:
        async with factory() as session:
            p1 = await _proj(session, 1)
            p2 = await _proj(session, 2)
            await session.commit()
            await asyncio.gather(
                advance_project(session, p1, _FakeBot()),
                advance_project(session, p2, _FakeBot()),
            )
    finally:
        await engine.dispose()

    # По одному: enter/exit не переплетаются (A,A,B,B или B,B,A,A)
    assert len(order) == 4
    pairs = [(order[0], order[1]), (order[2], order[3])]
    for enter, exit_ in pairs:
        assert exit_ == -enter


@pytest.mark.asyncio
async def test_video_fail_skip_after_5(env, monkeypatch) -> None:
    """После 5 ошибок подряд кадр пропускается (_claim не берёт)."""
    engine, factory = env
    monkeypatch.setattr("app.db.SessionLocal", factory)
    try:
        async with factory() as session:
            p = await _proj_status(session, 1, ProjectStatus.generating_videos)
            fr = Frame(
                project_id=p.id, number=1, attrs={}, voiceover_text="t"
            )
            session.add(fr)
            await session.commit()

            for _ in range(5):
                await gv._note_video_fail_db(p.id, fr.id, RuntimeError("boom"))

            await session.refresh(fr)
            assert (fr.attrs or {}).get(gv.VIDEO_SKIP_ATTR)
            claimed = await gv._claim_shot1_video_batch(session, p.id, limit=4)
            assert all(c.id != fr.id for c in claimed)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_video_fail_counter_reset(env, monkeypatch) -> None:
    engine, factory = env
    monkeypatch.setattr("app.db.SessionLocal", factory)
    try:
        async with factory() as session:
            p = await _proj_status(session, 1, ProjectStatus.generating_videos)
            fr = Frame(
                project_id=p.id, number=1, attrs={}, voiceover_text="t"
            )
            session.add(fr)
            await session.commit()

            await gv._note_video_fail_db(p.id, fr.id, RuntimeError("x"))
            await gv._note_video_fail_db(p.id, fr.id, RuntimeError("x"))
            await session.refresh(fr)
            assert (fr.attrs or {}).get(gv.VIDEO_FAIL_ATTR) == 2

            await gv._reset_video_fail_db(p.id, fr.id)
            await session.refresh(fr)
            assert not (fr.attrs or {}).get(gv.VIDEO_FAIL_ATTR)
            assert not (fr.attrs or {}).get(gv.VIDEO_SKIP_ATTR)
    finally:
        await engine.dispose()
