"""project_control: stop не сбрасывает auto_mode."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
async def test_stop_running_preserves_auto_mode(session: AsyncSession) -> None:
    p = Project(
        slug="t",
        topic="Test",
        status=ProjectStatus.enriching_3,
        auto_mode=True,
        meta={"enrich_completed_slots": [1, 2]},
    )
    session.add(p)
    await session.flush()

    info = await stop_project_running(session, p)

    assert info["ok"] is True
    assert info["stopped_kind"] == "running"
    assert p.auto_mode is True
    assert p.status is ProjectStatus.enrich_2_ready
    assert (p.meta or {}).get("user_stop") is True


@pytest.mark.asyncio
async def test_stop_enriching_without_prior_slots_goes_script_ready(
    session: AsyncSession,
) -> None:
    """Canvas: первая GPT-нода = slot 2. ⏹ не ставит ложный enrich_1_ready."""
    p = Project(
        slug="eg2-only",
        topic="Test",
        status=ProjectStatus.enriching_2,
        auto_mode=True,
        script_text="x" * 300,
        general_plan="y" * 300,
        meta={},
    )
    session.add(p)
    await session.flush()

    info = await stop_project_running(session, p)
    assert info["ok"] is True
    assert p.status is ProjectStatus.script_ready
    assert (p.meta or {}).get("user_stop") is True

    from app.orchestrator.auto_advance import maybe_auto_advance

    advanced = await maybe_auto_advance(session, p, bot=None)  # type: ignore[arg-type]
    assert advanced is False
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_stop_clears_failure_sleep(session: AsyncSession) -> None:
    p = Project(
        slug="sleep-stop",
        topic="Test",
        status=ProjectStatus.planning,
        auto_mode=True,
        meta={
            "step_failure": {
                "sleep_until": "2099-01-01T00:00:00+00:00",
                "total_fails": {"planning": 3},
            }
        },
    )
    session.add(p)
    await session.flush()

    await stop_project_running(session, p)

    assert p.status is ProjectStatus.new
    assert (p.meta or {}).get("user_stop") is True
    fs = (p.meta or {}).get("step_failure") or {}
    assert "sleep_until" not in fs
    assert fs.get("total_fails") == {"planning": 3}
