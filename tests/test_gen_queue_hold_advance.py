"""Gen queue: hold auto_advance after until_node target (including past target)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import maybe_auto_advance
from app.services.gen_queue_run import should_hold_queue_auto_advance


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _meta_script_target(*, complete: bool = False) -> dict:
    return {
        "gen_queue_run": {
            "mode": "until_node",
            "target_node_type": "script",
            "complete": complete,
        }
    }


def test_should_hold_at_script_ready():
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        meta=_meta_script_target(),
    )
    assert should_hold_queue_auto_advance(p)


def test_should_hold_past_script_target():
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.image_prompts_ready,
        auto_mode=True,
        meta=_meta_script_target(),
    )
    assert should_hold_queue_auto_advance(p)


def test_should_not_hold_full_mode():
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.image_prompts_ready,
        auto_mode=True,
        meta={"gen_queue_run": {"mode": "full", "complete": False}},
    )
    assert not should_hold_queue_auto_advance(p)


@pytest.mark.asyncio
async def test_maybe_auto_advance_holds_at_script_target(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.gen_queue.get_gen_queue", lambda: [8])
    p = Project(
        id=8,
        slug="t8",
        topic="t8",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
        meta=_meta_script_target(),
    )
    session.add(p)
    await session.flush()

    advanced = await maybe_auto_advance(session, p, bot=None)
    assert advanced is True
    assert p.status is ProjectStatus.script_ready
    assert (p.meta or {}).get("gen_queue_run", {}).get("complete") is True


@pytest.mark.asyncio
async def test_maybe_auto_advance_does_not_pass_script_target(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """script_ready + until_node=script не должен уйти в splitting."""
    monkeypatch.setattr("app.services.gen_queue.get_gen_queue", lambda: [8])
    p = Project(
        id=8,
        slug="t8",
        topic="t8",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
        meta=_meta_script_target(),
    )
    session.add(p)
    await session.flush()

    await maybe_auto_advance(session, p, bot=None)
    assert p.status is not ProjectStatus.splitting
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_maybe_auto_advance_blocked_by_user_stop(session: AsyncSession) -> None:
    p = Project(
        id=8,
        slug="t8",
        topic="t8",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
        meta={**_meta_script_target(), "user_stop": True},
    )
    session.add(p)
    await session.flush()

    advanced = await maybe_auto_advance(session, p, bot=None)
    assert advanced is False
    assert p.status is ProjectStatus.script_ready
