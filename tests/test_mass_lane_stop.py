"""STOP на mass-lane: не перезапускать и снять stop-флаг."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import serial_next_mass_lane, serial_tick_mass_lanes
from app.services.project_control import stop_project_running
from app.services.step_cancel import is_stop_requested, request_stop


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
async def test_stop_clears_stop_flag(session: AsyncSession) -> None:
    p = Project(slug="lane", topic="T", status=ProjectStatus.planning, auto_mode=True)
    p.meta = {"mass_parent_id": 8, "mass_lane_position": 1}
    session.add(p)
    await session.flush()

    request_stop(p.id)
    assert is_stop_requested(p.id) is True

    info = await stop_project_running(session, p)
    assert info["ok"] is True
    assert p.status is ProjectStatus.new
    assert is_stop_requested(p.id) is False


@pytest.mark.asyncio
async def test_stop_mass_lane_sets_user_stop_and_serial_skips(session: AsyncSession) -> None:
    parent = Project(slug="mass", topic="Mass", status=ProjectStatus.new, auto_mode=True)
    session.add(parent)
    await session.flush()
    child = Project(
        slug="lane1",
        topic="Lane",
        status=ProjectStatus.planning,
        auto_mode=True,
        meta={"mass_parent_id": parent.id, "mass_lane_position": 1},
    )
    session.add(child)
    await session.flush()

    await stop_project_running(session, child)
    assert child.status is ProjectStatus.new
    assert (child.meta or {}).get("mass_lane_user_stop") is True

    nxt = await serial_next_mass_lane(session, parent.id)
    assert nxt is None

    started = await serial_tick_mass_lanes(session)
    assert started == 0
