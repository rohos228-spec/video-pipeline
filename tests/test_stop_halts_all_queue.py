"""⏹ STOP = стоп всего: следующий mass-lane / gen_queue слот не стартует."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.settings as app_settings
from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import serial_tick_mass_lanes
from app.services.gen_queue import gen_queue_tick
from app.services.project_control import stop_project_running
from app.services.sidebar_layout import (
    clear_gen_queue_halted,
    is_gen_queue_halted,
    set_gen_queue,
)


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    clear_gen_queue_halted(reason="test setup")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_stop_mass_lane_does_not_start_next_sibling(
    session: AsyncSession,
) -> None:
    parent = Project(slug="mass", topic="Mass", status=ProjectStatus.new, auto_mode=True)
    parent.meta = {"mass_factory": True}
    session.add(parent)
    await session.flush()

    c1 = Project(
        slug="lane1",
        topic="A",
        status=ProjectStatus.planning,
        auto_mode=True,
        meta={"mass_parent_id": parent.id, "mass_lane_position": 1},
    )
    c2 = Project(
        slug="lane2",
        topic="B",
        status=ProjectStatus.new,
        auto_mode=True,
        meta={"mass_parent_id": parent.id, "mass_lane_position": 2},
    )
    c3 = Project(
        slug="lane3",
        topic="C",
        status=ProjectStatus.new,
        auto_mode=True,
        meta={"mass_parent_id": parent.id, "mass_lane_position": 3},
    )
    session.add_all([c1, c2, c3])
    await session.flush()

    await stop_project_running(session, c1)
    await session.refresh(parent)
    await session.refresh(c2)
    await session.refresh(c3)

    assert (parent.meta or {}).get("mass_family_halted") is True
    assert (c2.meta or {}).get("user_stop") is True
    assert (c2.meta or {}).get("mass_lane_user_stop") is True
    assert (c3.meta or {}).get("mass_lane_user_stop") is True
    assert is_gen_queue_halted() is True

    started = await serial_tick_mass_lanes(session)
    assert started == 0
    assert c2.status is ProjectStatus.new
    assert c3.status is ProjectStatus.new


@pytest.mark.asyncio
async def test_stop_halts_gen_queue_tick(
    session: AsyncSession,
    monkeypatch,
) -> None:
    p1 = Project(
        slug="q1",
        topic="t1",
        status=ProjectStatus.planning,
        auto_mode=True,
    )
    p2 = Project(
        slug="q2",
        topic="t2",
        status=ProjectStatus.new,
        auto_mode=True,
    )
    session.add_all([p1, p2])
    await session.flush()
    set_gen_queue([p1.id, p2.id])

    await stop_project_running(session, p1)
    assert is_gen_queue_halted() is True
    await session.refresh(p2)
    assert (p2.meta or {}).get("user_stop") is True

    started = await gen_queue_tick(session)
    assert started == 0
    assert p2.status is ProjectStatus.new
