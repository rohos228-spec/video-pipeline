"""STOP (user_stop): железная блокировка auto_advance самого проекта.

В очереди (top-N окно) слот с user_stop/paused ПРОПУСКАЕТСЯ — хвост
очереди продолжает работу, см. tests/test_gen_queue_parallel.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.auto_advance import maybe_auto_advance
from app.services.gen_queue import (
    gen_queue_blocks_project,
    gen_queue_tick,
    gen_queue_window_projects,
)
from app.services.gen_queue_run import is_user_stopped
from app.services.project_control import stop_project_running
from app.services.step_data_guard import clamp_status_to_data


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
    import app.settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_stop_on_script_ready_sets_user_stop(session: AsyncSession) -> None:
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
    )
    session.add(p)
    await session.flush()

    info = await stop_project_running(session, p)

    assert info["ok"] is True
    assert info["stopped_kind"] == "gate"
    assert is_user_stopped(p)
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_maybe_auto_advance_blocked_after_stop_on_ready(
    session: AsyncSession,
) -> None:
    p = Project(
        id=2,
        slug="t2",
        topic="t2",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
    )
    session.add(p)
    await session.flush()
    await stop_project_running(session, p)

    advanced = await maybe_auto_advance(session, p, bot=None)
    assert advanced is False
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_stop_generating_hero_does_not_auto_restart(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⏹ во время hero → frames_ready; auto_mode не должен снова стартовать hero.

    Live-баг 2026-07-30: после стопа stale HITL approved / auto-approve
    снова жёг generating_hero («повторный запуск ноды после стопа»).
    """
    from app.models import HITLDecision, HITLKind, HITLRequest
    from app.services.project_control import auto_awaits_manual_start

    p = Project(
        slug="hero-stop",
        topic="t",
        status=ProjectStatus.generating_hero,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
        meta={},
    )
    session.add(p)
    await session.flush()
    session.add(
        HITLRequest(
            project_id=p.id,
            kind=HITLKind.approve_hero,
            decision=HITLDecision.approved,
            payload={},
        )
    )
    await session.flush()

    async def _ok_ready(*_a, **_k):
        return True

    monkeypatch.setattr(
        "app.orchestrator.auto_advance.ready_status_confirmed_by_data",
        _ok_ready,
    )
    monkeypatch.setattr(
        "app.orchestrator.auto_advance.clamp_status_to_data",
        _ok_ready,
    )
    monkeypatch.setattr(
        "app.services.gen_queue.project_gated_by_gen_queue",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "app.services.gen_queue.gen_queue_blocks_project",
        lambda *_a, **_k: None,
    )

    info = await stop_project_running(session, p)
    assert info["ok"] is True
    assert p.status is ProjectStatus.frames_ready
    assert is_user_stopped(p)
    assert auto_awaits_manual_start(p)

    advanced = await maybe_auto_advance(session, p, bot=None)
    assert advanced is False
    assert p.status is ProjectStatus.frames_ready
    assert is_user_stopped(p)


@pytest.mark.asyncio
async def test_clamp_status_skips_user_stopped(session: AsyncSession) -> None:
    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        meta={"user_stop": True},
    )
    session.add(p)
    await session.flush()

    result = await clamp_status_to_data(session, p)
    assert result is None
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_user_stop_slot_skipped_in_gen_queue_window(
    session: AsyncSession,
    monkeypatch,
) -> None:
    """⏹ на #2 не блокирует #4: слот пропускается, окно достаётся хвосту.

    Железный gate user_stop действует на сам проект (его auto_advance
    заблокирован — см. test_maybe_auto_advance_blocked_after_stop_on_ready),
    но очередь дальше не стопорится.
    """
    monkeypatch.setattr(
        "app.services.gen_queue.get_gen_queue",
        lambda: [2, 4],
    )
    p2 = Project(
        id=2,
        slug="p2",
        topic="t",
        status=ProjectStatus.plan_ready,
        auto_mode=True,
        meta={"user_stop": True},
        general_plan="x" * 500,
    )
    p4 = Project(
        id=4,
        slug="p4",
        topic="t",
        status=ProjectStatus.plan_ready,
        auto_mode=True,
        general_plan="y" * 500,
    )
    session.add_all([p2, p4])
    await session.flush()

    assert await gen_queue_blocks_project(session, 4) is None
    window = await gen_queue_window_projects(session)
    assert [p.id for p in window] == [4]


@pytest.mark.asyncio
async def test_gen_queue_tick_does_not_autostart_new(
    session: AsyncSession,
    monkeypatch,
) -> None:
    """new+auto_mode в очереди: без ручного ▶ статус остаётся new."""
    monkeypatch.setattr(
        "app.services.gen_queue.get_gen_queue",
        lambda: [2, 4],
    )
    p2 = Project(
        id=2,
        slug="p2",
        topic="t",
        status=ProjectStatus.new,
        auto_mode=True,
        meta={"user_stop": True},
    )
    p4 = Project(
        id=4,
        slug="p4",
        topic="t",
        status=ProjectStatus.new,
        auto_mode=True,
    )
    session.add_all([p2, p4])
    await session.flush()

    started = await gen_queue_tick(session)
    assert started == 0
    assert p2.status is ProjectStatus.new
    assert p4.status is ProjectStatus.new


@pytest.mark.asyncio
async def test_gen_queue_tick_waits_on_user_stop_when_not_new(
    session: AsyncSession,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.gen_queue.get_gen_queue",
        lambda: [2, 4],
    )
    p2 = Project(
        id=2,
        slug="p2",
        topic="t",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        script_text="x" * 500,
        general_plan="y" * 500,
        meta={"user_stop": True},
    )
    p4 = Project(
        id=4,
        slug="p4",
        topic="t",
        status=ProjectStatus.new,
        auto_mode=True,
    )
    session.add_all([p2, p4])
    await session.flush()

    started = await gen_queue_tick(session)
    assert started == 0
    assert p4.status is ProjectStatus.new
