"""STOP (user_stop): железная блокировка auto_advance и очереди."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import maybe_auto_advance
from app.services.gen_queue import gen_queue_blocks_project, gen_queue_tick
from app.services.gen_queue_run import is_user_stopped
from app.services.project_control import stop_project_running
from app.services.step_data_guard import clamp_status_to_data


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
async def test_stop_on_script_ready_sets_user_stop(session: AsyncSession) -> None:
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
    )
    session.add(p)
    await session.flush()

    info = await stop_project_running(session, p)

    assert info["ok"] is True
    assert info["stopped_kind"] == "gate"
    assert is_user_stopped(p)
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_maybe_auto_advance_blocked_after_stop_on_ready(
    session: AsyncSession,
) -> None:
    p = Project(
        id=2,
        slug="t2",
        topic="t2",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
    )
    session.add(p)
    await session.flush()
    await stop_project_running(session, p)

    advanced = await maybe_auto_advance(session, p, bot=None)
    assert advanced is False
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_clamp_status_skips_user_stopped(session: AsyncSession) -> None:
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        meta={"user_stop": True},
    )
    session.add(p)
    await session.flush()

    result = await clamp_status_to_data(session, p)
    assert result is None
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_user_stop_blocks_later_in_gen_queue(
    session: AsyncSession,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.gen_queue.get_gen_queue",
        lambda: [2, 4],
    )
    p2 = Project(
        id=2,
        slug="p2",
        topic="t",
        status=ProjectStatus.plan_ready,
        auto_mode=True,
        meta={"user_stop": True},
        general_plan="x" * 500,
    )
    p4 = Project(
        id=4,
        slug="p4",
        topic="t",
        status=ProjectStatus.plan_ready,
        auto_mode=True,
        general_plan="y" * 500,
    )
    session.add_all([p2, p4])
    await session.flush()

    assert await gen_queue_blocks_project(session, 4) == 2


@pytest.mark.asyncio
async def test_gen_queue_tick_starts_head_despite_stale_user_stop(
    session: AsyncSession,
    monkeypatch,
) -> None:
    """new+auto_mode в очереди: устаревший user_stop не блокирует автостарт."""
    monkeypatch.setattr(
        "app.services.gen_queue.get_gen_queue",
        lambda: [2, 4],
    )
    p2 = Project(
        id=2,
        slug="p2",
        topic="t",
        status=ProjectStatus.new,
        auto_mode=True,
        meta={"user_stop": True},
    )
    p4 = Project(
        id=4,
        slug="p4",
        topic="t",
        status=ProjectStatus.new,
        auto_mode=True,
    )
    session.add_all([p2, p4])
    await session.flush()

    started = await gen_queue_tick(session)
    assert started == 1
    assert p2.status is ProjectStatus.planning
    assert (p2.meta or {}).get("user_stop") is None


@pytest.mark.asyncio
async def test_gen_queue_tick_waits_on_user_stop_when_not_new(
    session: AsyncSession,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.gen_queue.get_gen_queue",
        lambda: [2, 4],
    )
    p2 = Project(
        id=2,
        slug="p2",
        topic="t",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
        meta={"user_stop": True},
    )
    p4 = Project(
        id=4,
        slug="p4",
        topic="t",
        status=ProjectStatus.new,
        auto_mode=True,
    )
    session.add_all([p2, p4])
    await session.flush()

    started = await gen_queue_tick(session)
    assert started == 0
    assert p4.status is ProjectStatus.new
