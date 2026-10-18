"""Неблокирующее ревью #352: 3-й fail enrich всё ещё soft retry, затем сон."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus


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
async def test_enrich_third_fail_soft_retries_then_sleeps(session: AsyncSession) -> None:
    from app.services.step_failure_policy import record_step_failure

    first = Project(
        slug="enrich-fail-1",
        topic="t",
        status=ProjectStatus.enriching_1,
        auto_mode=False,
        meta={},
    )
    third = Project(
        slug="enrich-fail-3",
        topic="t",
        status=ProjectStatus.enriching_1,
        auto_mode=False,
        meta={"step_failure": {"total_fails": {"enriching_1": 2}}},
    )
    session.add(first)
    session.add(third)
    await session.flush()

    with (
        patch(
            "app.services.step_failure_policy._soft_retry_without_wipe",
            new_callable=AsyncMock,
        ) as soft,
        patch(
            "app.services.step_failure_policy._fail_active_on_both",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.step_failure_policy._push_parked_runtime",
            new_callable=AsyncMock,
        ),
    ):
        first_action = await record_step_failure(
            session, first, error=RuntimeError("gpt down")
        )
        assert first_action == "stopped"
        soft.assert_not_awaited()

        third_action = await record_step_failure(
            session, third, error=RuntimeError("gpt down")
        )
    assert third_action == "sleep"
    soft.assert_awaited()
    assert third.status is ProjectStatus.paused
