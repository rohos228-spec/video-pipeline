"""W4-4 (C08): профиль ожидания отказов шага. Юнит и сухой прогон на SQLite в памяти, без сети.

Файл назван ``*_test.py``, чтобы pytest его собирал при префиксе ``w4_4_``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services import step_failure_policy as sfp


@pytest.fixture(autouse=True)
def _no_profile_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sfp.WAIT_PROFILE_ENV, raising=False)


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _project(meta: dict | None = None, status=ProjectStatus.planning, slug="w44") -> Project:
    return Project(slug=slug, topic="t", status=status, auto_mode=True, meta=meta or {})


def test_default_profile_is_the_old_safe_scheme() -> None:
    profile = sfp.wait_profile(_project())
    assert profile.name == "safe"
    assert (profile.fails_per_cycle, profile.max_cycles, profile.sleep_minutes) == (3, 3, 30)
    assert profile.max_total_fails == sfp.MAX_TOTAL_FAILS == 9
    assert profile.abandon_on_fatal_config is False
    assert sfp.abandon_threshold(None) == 9


def test_fast_profile_from_env_and_the_project_overrides_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sfp.WAIT_PROFILE_ENV, "fast")
    fast = sfp.wait_profile(_project())
    assert fast.name == "fast" and fast.max_total_fails == 4 and fast.sleep_minutes == 3
    assert sfp.abandon_threshold(_project()) == 4
    # проект сильнее окружения
    assert sfp.wait_profile(_project({"step_failure_profile": "safe"})).name == "safe"
    monkeypatch.delenv(sfp.WAIT_PROFILE_ENV)
    assert sfp.wait_profile(_project({"step_failure_profile": "fast"})).name == "fast"
    # неизвестное имя - безопасный профиль
    assert sfp.wait_profile(_project({"step_failure_profile": "turbo"})).name == "safe"


def test_stand_overrides_of_safe_constants_are_still_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """scripts/e2e_vibecode_script_frames_qc.py подменяет SLEEP_MINUTES в модуле: профиль читает его на лету."""
    monkeypatch.setattr(sfp, "SLEEP_MINUTES", 1)
    monkeypatch.setattr(sfp, "XLSX_SHEET_FORMAT_SLEEP_MINUTES", 1)
    profile = sfp.wait_profile(_project())
    assert profile.sleep_minutes == 1 and profile.quick_sleep_minutes == 1
    assert sfp.sleep_minutes_for_error(RuntimeError("other"), profile) == 1


def test_sleep_minutes_follow_the_profile_and_keep_the_one_argument_form() -> None:
    err = RuntimeError("other")
    assert sfp.sleep_minutes_for_error(err) == sfp.SLEEP_MINUTES
    assert sfp.sleep_minutes_for_error(err, sfp.wait_profile(_project({"step_failure_profile": "fast"}))) == 3
    quick = RuntimeError("Cannot connect to host x")
    assert sfp.sleep_minutes_for_error(quick) == sfp.XLSX_SHEET_FORMAT_SLEEP_MINUTES
    assert sfp.sleep_minutes_for_error(quick, sfp.wait_profile(_project({"step_failure_profile": "fast"}))) == 1


@pytest.mark.asyncio
async def test_safe_profile_still_needs_nine_failures(session: AsyncSession) -> None:
    p = _project({"step_failure": {"total_fails": {"planning": 7}}})
    session.add(p)
    await session.flush()
    with patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock):
        action = await sfp.record_step_failure(session, p, error=RuntimeError("Некорректный xlsx: пустой план"))
    assert action == "retry"  # 8-й отказ: ещё не порог
    assert p.meta["step_failure"]["total_fails"]["planning"] == 8


@pytest.mark.asyncio
async def test_fast_profile_abandons_after_four_failures_with_short_sleeps(session: AsyncSession) -> None:
    p = _project({"step_failure_profile": "fast"})
    session.add(p)
    await session.flush()
    actions = []
    with patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock):
        for _ in range(4):
            if p.status is ProjectStatus.paused:
                p.status = ProjectStatus.planning  # как делает рабочий цикл после сна
            actions.append(await sfp.record_step_failure(session, p, error=RuntimeError("Некорректный xlsx: пустой план")))
            if actions[-1] == "sleep":
                fs = p.meta["step_failure"]
                assert fs["recovery_cycles"] == 1
    assert actions == ["retry", "sleep", "retry", "abandon"]
    fs = p.meta["step_failure"]
    assert fs["total_fails"]["planning"] == 4
    assert fs["abandoned_reason"] == "retries_exhausted" and fs["abandoned_threshold"] == 4
    assert fs["wait_profile"] == "fast"
    assert p.status is ProjectStatus.paused
    assert sfp.is_retry_exhausted(p, "planning") is True


@pytest.mark.asyncio
async def test_fast_profile_sleep_is_three_minutes_not_thirty(session: AsyncSession) -> None:
    p = _project({"step_failure_profile": "fast", "step_failure": {"total_fails": {"planning": 1}}})
    session.add(p)
    await session.flush()
    with patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock):
        action = await sfp.record_step_failure(session, p, error=RuntimeError("some unknown failure"))
    assert action == "sleep"
    from datetime import datetime, timezone

    until = datetime.fromisoformat(p.meta["step_failure"]["sleep_until"])
    minutes = (until - datetime.now(timezone.utc)).total_seconds() / 60
    assert 2.0 < minutes <= 3.01


@pytest.mark.asyncio
async def test_fast_profile_closes_a_configuration_error_at_once_and_opens_no_recovery(session: AsyncSession) -> None:
    p = _project({"step_failure_profile": "fast"})
    session.add(p)
    await session.flush()
    with patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock), patch(
        "app.services.error_catalog.describe_error", return_value=("gpt_auth", "Неверный ключ")
    ):
        action = await sfp.record_step_failure(session, p, error=RuntimeError("401"))
    assert action == "abandon"
    fs = p.meta["step_failure"]
    assert fs["total_fails"]["planning"] == 1 and fs["abandoned_reason"] == "fatal_config"
    # повтор ключ не вылечит: автоматическое восстановление по этому закрытию не открывается
    assert sfp.is_retry_exhausted(p, "planning") is False


@pytest.mark.asyncio
async def test_safe_profile_does_not_close_a_configuration_error_early(session: AsyncSession) -> None:
    p = _project()
    session.add(p)
    await session.flush()
    with patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock), patch(
        "app.services.error_catalog.describe_error", return_value=("gpt_auth", "Неверный ключ")
    ):
        action = await sfp.record_step_failure(session, p, error=RuntimeError("401"))
    assert action == "retry"
    assert "abandoned_at" not in p.meta["step_failure"]


def test_is_retry_exhausted_for_a_legacy_safe_state_without_the_recorded_threshold() -> None:
    legacy = _project({"step_failure": {"abandoned_at": "x", "total_fails": {"planning": 9}}})
    assert sfp.is_retry_exhausted(legacy, "planning") is True
    short = _project({"step_failure": {"abandoned_at": "x", "total_fails": {"planning": 4}}})
    assert sfp.is_retry_exhausted(short, "planning") is False  # 4 отказов safe-профиля - ещё не порог
    assert sfp.is_retry_exhausted(None) is False


def test_the_recorded_threshold_survives_a_later_profile_change(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _project({"step_failure": {"abandoned_at": "x", "abandoned_threshold": 4, "total_fails": {"planning": 4}}})
    assert sfp.exhausted_threshold(p) == 4
    monkeypatch.setenv(sfp.WAIT_PROFILE_ENV, "safe")
    assert sfp.is_retry_exhausted(p, "planning") is True


def test_success_clears_the_closing_marks_but_not_the_history() -> None:
    p = _project({"step_failure": {"abandoned_at": "x", "abandoned_reason": "retries_exhausted",
                                    "abandoned_threshold": 4, "total_fails": {"planning": 4}}})
    sfp.clear_failure_on_success(p, ProjectStatus.planning)
    fs = p.meta["step_failure"]
    assert "abandoned_reason" not in fs and "abandoned_threshold" not in fs
    assert fs["total_fails"] == {}
