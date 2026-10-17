from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import continue_project_pipeline
from app.services.gen_queue_run import is_user_stopped
from app.services.project_control import auto_awaits_manual_start


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _stopped_project(session: AsyncSession) -> Project:
    p = Project(slug="t", topic="t", status=ProjectStatus.script_ready, auto_mode=False,
                meta={"user_stop": True, "auto_await_manual_start": True})
    session.add(p)
    await session.commit()
    return p


async def test_continue_clears_user_stop_and_manual_start_gate_in_the_database(session):
    p = await _stopped_project(session)
    info = await continue_project_pipeline(session, p, bot=None)
    await session.commit()
    assert "user_stop" in info["cleared"]
    await session.refresh(p)
    assert not is_user_stopped(p), "continue reported cleared but the flag survived a reload"
    assert not auto_awaits_manual_start(p)


async def test_continue_without_stop_flags_reports_nothing_cleared(session):
    p = Project(slug="t2", topic="t", status=ProjectStatus.script_ready, auto_mode=False, meta={})
    session.add(p)
    await session.commit()
    info = await continue_project_pipeline(session, p, bot=None)
    assert info["cleared"] == []
