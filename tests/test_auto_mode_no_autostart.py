"""Включение auto_mode не стартует шаги — только после ручного ▶."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import maybe_auto_advance
from app.services.project_control import (
    arm_auto_await_manual_start,
    auto_awaits_manual_start,
    clear_auto_await_manual_start,
    on_auto_mode_changed,
)
from app.services.project_steps import start_step


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_on_auto_mode_on_at_ready_arms_gate() -> None:
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.frames_ready,
        auto_mode=False,
        meta={},
    )
    on_auto_mode_changed(p, was_auto=False, now_auto=True)
    assert auto_awaits_manual_start(p) is True


def test_on_auto_mode_on_while_running_does_not_arm() -> None:
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.generating_hero,
        auto_mode=False,
        meta={},
    )
    on_auto_mode_changed(p, was_auto=False, now_auto=True)
    assert auto_awaits_manual_start(p) is False


def test_on_auto_mode_off_clears_gate() -> None:
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.plan_ready,
        auto_mode=True,
        meta={"auto_await_manual_start": True},
    )
    on_auto_mode_changed(p, was_auto=True, now_auto=False)
    assert auto_awaits_manual_start(p) is False


@pytest.mark.asyncio
async def test_maybe_auto_advance_blocked_until_manual_start(
    session: AsyncSession,
) -> None:
    p = Project(
        slug="aa-gate",
        topic="t",
        status=ProjectStatus.frames_ready,
        auto_mode=True,
        general_plan="x" * 200,
        script_text="y" * 200,
        meta={"split_completed": True, "auto_await_manual_start": True},
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)

    advanced = await maybe_auto_advance(session, p, bot=None, force=True)
    assert advanced is False
    assert p.status is ProjectStatus.frames_ready


@pytest.mark.asyncio
async def test_start_step_clears_gate_then_auto_can_continue(
    session: AsyncSession, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")

    p = Project(
        slug="aa-clear",
        topic="t",
        status=ProjectStatus.frames_ready,
        auto_mode=True,
        general_plan="x" * 200,
        script_text="y" * 200,
        meta={"split_completed": True, "auto_await_manual_start": True},
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)

    assert auto_awaits_manual_start(p) is True
    await start_step(session, p, "hero", explicit_ui_start=True, skip_queue_guard=True)
    assert auto_awaits_manual_start(p) is False
    assert p.status is ProjectStatus.generating_hero


def test_arm_idempotent() -> None:
    p = Project(slug="t", topic="t", status=ProjectStatus.new, meta={})
    assert arm_auto_await_manual_start(p) is True
    assert arm_auto_await_manual_start(p) is False
    assert clear_auto_await_manual_start(p) is True
    assert clear_auto_await_manual_start(p) is False
