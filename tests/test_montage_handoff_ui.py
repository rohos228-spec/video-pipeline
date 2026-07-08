"""Montage handoff: UI/run_sync видит сборку «в работе» при ожидании hub."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.run_sync import _derived_node_states
from app.settings import settings


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
async def test_assemble_running_when_handoff_pending(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "fleet_enabled", True)
    monkeypatch.setattr(settings, "fleet_role", "agent")
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.music_ready,
        meta={
            "fleet_montage_deferred": True,
            "montage_ready": True,
            "node_step_params": {"assemble": {"send_to_main_pc": True}},
        },
    )
    session.add(p)
    await session.flush()

    derived = await _derived_node_states(session, p)
    assert derived["assemble"].value == "running"
