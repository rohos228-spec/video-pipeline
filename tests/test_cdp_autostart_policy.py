"""CDP autostart + pause_infra вместо 9 ретраев."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bots.chrome_cdp import ChromeCdpUnavailableError
from app.models import Base, Project, ProjectStatus
from app.services.step_failure_policy import record_step_failure


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
async def test_cdp_error_pauses_immediately_not_nine_retries(
    session: AsyncSession,
) -> None:
    p = Project(
        slug="cdp",
        topic="t",
        status=ProjectStatus.planning,
        auto_mode=True,
    )
    session.add(p)
    await session.flush()

    action = await record_step_failure(
        session,
        p,
        error=ChromeCdpUnavailableError("CDP down"),
    )
    assert action == "pause_infra"
    assert p.status is ProjectStatus.paused
    meta = p.meta or {}
    fs = meta.get("step_failure") or {}
    assert fs.get("infra_pause") == "chrome_cdp"
    assert (fs.get("total_fails") or {}).get("planning") is None
