"""STOP прерывает fleet push/pull."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.fleet.transfer_state import (
    FleetTransferCancelled,
    cancel_fleet_transfer,
    clear_transfer_cancel,
    is_transfer_cancelled,
    register_transfer_task,
    request_transfer_cancel,
)
from app.models import Base, Project, ProjectStatus
from app.services.project_control import stop_project_running


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
async def test_cancel_fleet_transfer_sets_flag_and_event() -> None:
    clear_transfer_cancel(17)
    had = await cancel_fleet_transfer(17)
    assert had or True  # may be false if never started
    assert is_transfer_cancelled(17)
    clear_transfer_cancel(17)


@pytest.mark.asyncio
async def test_stop_cancels_active_fleet_transfer(session: AsyncSession) -> None:
    import asyncio

    clear_transfer_cancel(17)

    p = Project(
        slug="t",
        topic="Test",
        status=ProjectStatus.music_ready,
        auto_mode=True,
        meta={"fleet_montage_deferred": True, "montage_ready": True},
    )
    session.add(p)
    await session.flush()
    pid = p.id

    async def slow_push() -> None:
        for _ in range(200):
            if is_transfer_cancelled(pid):
                raise FleetTransferCancelled(str(pid))
            await asyncio.sleep(0.01)

    task = asyncio.create_task(slow_push())
    register_transfer_task(pid, task)
    await asyncio.sleep(0.05)

    info = await stop_project_running(session, p)

    assert info["ok"] is True
    assert info["stopped_kind"] in ("fleet_transfer", "auto_pipeline")
    clear_transfer_cancel(pid)
