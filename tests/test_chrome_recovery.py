"""Chrome recovery: перезапуск вместо 9× abandon."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bots.chrome_cdp import ChromeCdpUnavailableError
from app.models import Base, Project, ProjectStatus
from app.services.chrome_recovery import (
    MAX_CHROME_RESTARTS_PER_STEP,
    handle_chrome_step_failure,
    is_chrome_infra_error,
)
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


def test_is_chrome_infra_error() -> None:
    assert is_chrome_infra_error(ChromeCdpUnavailableError("x"))
    assert is_chrome_infra_error(
        RuntimeError("Cannot connect to host 127.0.0.1:29229")
    )


@pytest.mark.asyncio
async def test_chrome_failure_restarts_and_retries(session: AsyncSession) -> None:
    p = Project(
        slug="cdp",
        topic="t",
        status=ProjectStatus.planning,
        auto_mode=True,
    )
    session.add(p)
    await session.flush()

    with patch(
        "app.services.chrome_recovery.restart_chrome_for_pipeline",
        new_callable=AsyncMock,
        return_value=True,
    ):
        action = await handle_chrome_step_failure(
            session,
            p,
            ChromeCdpUnavailableError("CDP down"),
        )

    assert action == "retry"
    assert p.status is ProjectStatus.planning
    meta = p.meta or {}
    assert (meta.get("chrome_recovery") or {}).get("restart_attempts") == 1


@pytest.mark.asyncio
async def test_record_step_failure_delegates_to_chrome_recovery(
    session: AsyncSession,
) -> None:
    p = Project(
        slug="cdp2",
        topic="t",
        status=ProjectStatus.planning,
        auto_mode=True,
    )
    session.add(p)
    await session.flush()

    with patch(
        "app.services.chrome_recovery.restart_chrome_for_pipeline",
        new_callable=AsyncMock,
        return_value=True,
    ):
        action = await record_step_failure(
            session,
            p,
            error=ChromeCdpUnavailableError("CDP down"),
        )

    assert action == "retry"
    fs = (p.meta or {}).get("step_failure") or {}
    assert (fs.get("total_fails") or {}).get("planning") is None


@pytest.mark.asyncio
async def test_chrome_failure_pauses_after_max_restarts(session: AsyncSession) -> None:
    p = Project(
        slug="cdp3",
        topic="t",
        status=ProjectStatus.planning,
        auto_mode=True,
        meta={
            "chrome_recovery": {
                "restart_attempts": MAX_CHROME_RESTARTS_PER_STEP,
            }
        },
    )
    session.add(p)
    await session.flush()

    with patch(
        "app.services.chrome_recovery.restart_chrome_for_pipeline",
        new_callable=AsyncMock,
        return_value=False,
    ):
        action = await handle_chrome_step_failure(
            session,
            p,
            ChromeCdpUnavailableError("still down"),
        )

    assert action == "pause_infra"
    assert p.status is ProjectStatus.paused
