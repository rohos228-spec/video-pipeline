"""Tests for step_failure_policy counters (9 fails = 3 cycles × 3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.step_failure_policy import (
    FAILS_PER_CYCLE,
    MAX_CYCLES,
    MAX_TOTAL_FAILS,
    SLEEP_MINUTES,
    XLSX_SHEET_FORMAT_SLEEP_MINUTES,
    clear_failure_backoff_for_manual_start,
    clear_failure_sleep,
    is_sleeping,
    maybe_resume_after_sleep,
    record_step_failure,
    sleep_minutes_for_error,
)


def test_constants() -> None:
    assert FAILS_PER_CYCLE == 3
    assert MAX_CYCLES == 3
    assert MAX_TOTAL_FAILS == 9


def test_cycle_mapping() -> None:
    for total, expect_cycle, expect_in_cycle, sleep, abandon in [
        (1, 1, 1, False, False),
        (2, 1, 2, False, False),
        (3, 1, 3, True, False),
        (4, 2, 1, False, False),
        (6, 2, 3, True, False),
        (7, 3, 1, False, False),
        (9, 3, 3, False, True),
    ]:
        cycle = (total - 1) // FAILS_PER_CYCLE + 1
        in_cycle = ((total - 1) % FAILS_PER_CYCLE) + 1
        assert cycle == expect_cycle, total
        assert in_cycle == expect_in_cycle, total
        assert (total % FAILS_PER_CYCLE == 0) == sleep or abandon, total
        assert (total >= MAX_TOTAL_FAILS) == abandon, total


def test_manual_start_clears_sleep_and_fail_counter() -> None:
    p = Project(
        id=1,
        slug="t",
        status=ProjectStatus.generating_image_prompts,
        meta={
            "step_failure": {
                "sleep_until": "2099-01-01T00:00:00+00:00",
                "total_fails": {"generating_image_prompts": 3, "splitting": 1},
            }
        },
    )
    assert is_sleeping(p)
    assert clear_failure_backoff_for_manual_start(
        p, running_key="generating_image_prompts"
    )
    assert not is_sleeping(p)
    assert p.meta["step_failure"]["total_fails"] == {"splitting": 1}


def test_clear_failure_sleep_keeps_fail_counters() -> None:
    p = Project(
        id=1,
        slug="t",
        status=ProjectStatus.new,
        meta={
            "step_failure": {
                "sleep_until": "2099-01-01T00:00:00+00:00",
                "total_fails": {"planning": 3},
            }
        },
    )
    assert clear_failure_sleep(p)
    assert not is_sleeping(p)
    assert p.meta["step_failure"]["total_fails"] == {"planning": 3}
    assert clear_failure_sleep(p) is False


def test_sleep_minutes_shorter_for_xlsx_sheet_mismatch() -> None:
    err = RuntimeError(
        "скачанный xlsx невалиден: ошибка формата эксель таблицы: листы [a] "
        "не совпадают с шаблоном"
    )
    assert sleep_minutes_for_error(err) == XLSX_SHEET_FORMAT_SLEEP_MINUTES
    assert sleep_minutes_for_error(RuntimeError("other")) == SLEEP_MINUTES
    assert (
        sleep_minutes_for_error(
            RuntimeError(
                "xlsx writeback всё ещё неполный после 3 continue "
                "(model marked CONTINUE_XLSX; sheet='план' @row=56)"
            )
        )
        == XLSX_SHEET_FORMAT_SLEEP_MINUTES
    )
    assert (
        sleep_minutes_for_error(
            RuntimeError(
                "project_file: модель не вернула TSV `# Лист:`/`@row=` — "
                "project.xlsx не изменён"
            )
        )
        == XLSX_SHEET_FORMAT_SLEEP_MINUTES
    )


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
async def test_record_step_failure_user_stop_does_not_revive(
    session: AsyncSession,
) -> None:
    p = Project(
        slug="stop-plan",
        topic="t",
        status=ProjectStatus.planning,
        auto_mode=True,
        meta={"user_stop": True},
    )
    session.add(p)
    await session.flush()

    with patch(
        "app.services.run_sync.mark_running_node_failed",
        new_callable=AsyncMock,
    ):
        action = await record_step_failure(
            session,
            p,
            error=RuntimeError(
                "Некорректный xlsx: ChatGPT вернул пустой/слишком короткий план "
                "после xlsx-sync"
            ),
        )

    assert action == "stopped"
    assert p.status is ProjectStatus.new
    fs = (p.meta or {}).get("step_failure") or {}
    assert "sleep_until" not in fs
    assert (fs.get("total_fails") or {}).get("planning") is None


@pytest.mark.asyncio
async def test_record_step_failure_sleep_leaves_running_status(
    session: AsyncSession,
) -> None:
    p = Project(
        slug="sleep-plan",
        topic="t",
        status=ProjectStatus.planning,
        auto_mode=True,
        meta={"step_failure": {"total_fails": {"planning": 2}}},
    )
    session.add(p)
    await session.flush()

    with patch(
        "app.services.run_sync.mark_running_node_failed",
        new_callable=AsyncMock,
    ):
        action = await record_step_failure(
            session,
            p,
            error=RuntimeError("Некорректный xlsx: пустой план"),
        )

    assert action == "sleep"
    assert p.status is ProjectStatus.new
    assert is_sleeping(p)
    fs = (p.meta or {}).get("step_failure") or {}
    assert fs["total_fails"]["planning"] == 3
    assert fs.get("last_running") == "planning"


@pytest.mark.asyncio
async def test_maybe_resume_after_sleep_skips_user_stop(
    session: AsyncSession,
) -> None:
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    p = Project(
        slug="resume-stop",
        topic="t",
        status=ProjectStatus.new,
        auto_mode=True,
        meta={
            "user_stop": True,
            "step_failure": {
                "sleep_until": past,
                "last_running": "planning",
                "total_fails": {"planning": 3},
            },
        },
    )
    session.add(p)
    await session.flush()

    assert await maybe_resume_after_sleep(session, p) is False
    assert p.status is ProjectStatus.new
