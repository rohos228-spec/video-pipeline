"""Gen queue top-N window (WORKER_MAX_PARALLEL > 1)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.gen_queue import (
    gen_queue_blocks_project,
    gen_queue_busy_count,
    gen_queue_busy_projects,
    gen_queue_reconcile,
    gen_queue_window_projects,
)


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
    db_path = tmp_path / "gq_par.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        monkeypatch.setattr(
            "app.services.gen_queue.get_gen_queue",
            lambda: [1, 2, 3, 4],
        )
        monkeypatch.setattr(
            "app.services.gen_queue.is_gen_queue_halted",
            lambda: False,
        )
        monkeypatch.setattr(
            "app.services.gen_queue.worker_max_parallel",
            lambda: 2,
        )
        yield s
    await engine.dispose()


async def _add(
    session: AsyncSession,
    pid: int,
    *,
    status: ProjectStatus,
) -> Project:
    from app.services.gen_queue_run import set_gen_queue_run

    p = Project(
        id=pid,
        slug=f"p{pid}",
        topic=f"t{pid}",
        status=status,
        auto_mode=True,
        meta={},
    )
    session.add(p)
    await session.flush()
    await set_gen_queue_run(
        session, p, mode="until_node", target_node_type="script"
    )
    return p


@pytest.mark.asyncio
async def test_window_size_follows_max_parallel(session: AsyncSession) -> None:
    await _add(session, 1, status=ProjectStatus.planning)
    await _add(session, 2, status=ProjectStatus.scripting)
    await _add(session, 3, status=ProjectStatus.plan_ready)
    await _add(session, 4, status=ProjectStatus.new)
    window = await gen_queue_window_projects(session)
    assert [p.id for p in window] == [1, 2]


@pytest.mark.asyncio
async def test_parallel_allows_second_while_first_busy(
    session: AsyncSession,
) -> None:
    await _add(session, 1, status=ProjectStatus.planning)
    await _add(session, 2, status=ProjectStatus.plan_ready)
    await _add(session, 3, status=ProjectStatus.plan_ready)
    assert await gen_queue_blocks_project(session, 1) is None
    assert await gen_queue_blocks_project(session, 2) is None
    assert await gen_queue_blocks_project(session, 3) == 2


@pytest.mark.asyncio
async def test_parallel_paused_still_hard_blocks(
    session: AsyncSession,
) -> None:
    await _add(session, 1, status=ProjectStatus.paused)
    await _add(session, 2, status=ProjectStatus.plan_ready)
    assert await gen_queue_blocks_project(session, 2) == 1


@pytest.mark.asyncio
async def test_reconcile_keeps_top_n_busy_rolls_rest(
    session: AsyncSession,
) -> None:
    p1 = await _add(session, 1, status=ProjectStatus.planning)
    p2 = await _add(session, 2, status=ProjectStatus.scripting)
    p3 = await _add(session, 3, status=ProjectStatus.splitting)
    rolled = await gen_queue_reconcile(session)
    await session.refresh(p1)
    await session.refresh(p2)
    await session.refresh(p3)
    assert rolled == 1
    assert p1.status is ProjectStatus.planning
    assert p2.status is ProjectStatus.scripting
    assert p3.status is not ProjectStatus.splitting
    busy = await gen_queue_busy_projects(session)
    assert busy == [1, 2]
    assert await gen_queue_busy_count(session) == 2


@pytest.mark.asyncio
async def test_serial_mode_unchanged_when_n_is_one(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.gen_queue.worker_max_parallel",
        lambda: 1,
    )
    await _add(session, 1, status=ProjectStatus.planning)
    await _add(session, 2, status=ProjectStatus.plan_ready)
    assert await gen_queue_blocks_project(session, 2) == 1
    window = await gen_queue_window_projects(session)
    assert [p.id for p in window] == [1]
