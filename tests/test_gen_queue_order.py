"""Gen queue: strict serial order — later projects wait for earlier."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.gen_queue import gen_queue_blocks_project, gen_queue_tick
from app.services.gen_queue_run import set_gen_queue_run


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
    db_path = tmp_path / "gq.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        monkeypatch.setattr(
            "app.services.gen_queue.get_gen_queue",
            lambda: [7, 8],
        )
        yield s
    await engine.dispose()


async def _add(
    session: AsyncSession,
    pid: int,
    *,
    status: ProjectStatus,
    until: str | None = None,
) -> Project:
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
    if until:
        await set_gen_queue_run(
            session, p, mode="until_node", target_node_type=until
        )
    return p


@pytest.mark.asyncio
async def test_blocks_later_while_earlier_at_script_ready_target_audio(
    session: AsyncSession,
) -> None:
    """#7 ждёт озвучку — #8 не должен продвигаться."""
    await _add(
        session, 7, status=ProjectStatus.script_ready, until="audio"
    )
    await _add(session, 8, status=ProjectStatus.plan_ready, until="script")
    assert await gen_queue_blocks_project(session, 8) == 7
    assert await gen_queue_blocks_project(session, 7) is None


@pytest.mark.asyncio
async def test_allows_later_when_earlier_queue_run_complete(
    session: AsyncSession,
) -> None:
    await _add(
        session, 7, status=ProjectStatus.script_ready, until="script"
    )
    await _add(session, 8, status=ProjectStatus.plan_ready, until="script")
    assert await gen_queue_blocks_project(session, 8) is None


@pytest.mark.asyncio
async def test_blocks_later_not_blocked_by_paused_earlier(
    session: AsyncSession,
) -> None:
    await _add(session, 7, status=ProjectStatus.paused, until="script")
    await _add(session, 8, status=ProjectStatus.plan_ready, until="script")
    assert await gen_queue_blocks_project(session, 8) == 7


@pytest.mark.asyncio
async def test_user_stop_blocks_later_in_queue(
    session: AsyncSession,
) -> None:
    await _add(session, 7, status=ProjectStatus.plan_ready, until="script")
    p7 = await session.get(Project, 7)
    assert p7 is not None
    p7.meta = {**(p7.meta or {}), "user_stop": True}
    await session.flush()
    await _add(session, 8, status=ProjectStatus.plan_ready, until="script")
    assert await gen_queue_blocks_project(session, 8) == 7


@pytest.mark.asyncio
async def test_gen_queue_normalize_sorts_by_project_id(
    monkeypatch,
) -> None:
    from app.services.sidebar_layout import _normalize_gen_queue

    assert _normalize_gen_queue([4, 1, 3, 2]) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_gen_queue_tick_skips_paused_starts_next(
    session: AsyncSession,
) -> None:
    await _add(session, 7, status=ProjectStatus.paused, until="script")
    await _add(session, 8, status=ProjectStatus.new, until="script")
    started = await gen_queue_tick(session)
    assert started == 1
    p8 = await session.get(Project, 8)
    assert p8 is not None
    assert p8.status is ProjectStatus.planning


@pytest.mark.asyncio
async def test_gen_queue_tick_starts_next_only_after_earlier_done(
    session: AsyncSession,
) -> None:
    await _add(
        session, 7, status=ProjectStatus.script_ready, until="script"
    )
    await _add(session, 8, status=ProjectStatus.new, until="script")
    started = await gen_queue_tick(session)
    assert started == 1
    p8 = await session.get(Project, 8)
    assert p8 is not None
    assert p8.status is ProjectStatus.planning
