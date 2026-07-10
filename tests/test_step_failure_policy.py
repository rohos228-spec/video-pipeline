"""Tests for step_failure_policy counters (9 fails = 3 cycles × 3)."""

from app.models import Project, ProjectStatus
from app.services.step_failure_policy import (
    FAILS_PER_CYCLE,
    MAX_CYCLES,
    MAX_TOTAL_FAILS,
    SLEEP_MINUTES,
    XLSX_SHEET_FORMAT_SLEEP_MINUTES,
    clear_failure_backoff_for_manual_start,
    is_sleeping,
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


def test_sleep_minutes_shorter_for_xlsx_sheet_mismatch() -> None:
    err = RuntimeError(
        "скачанный xlsx невалиден: ошибка формата эксель таблицы: листы [a] "
        "не совпадают с шаблоном"
    )
    assert sleep_minutes_for_error(err) == XLSX_SHEET_FORMAT_SLEEP_MINUTES
    assert sleep_minutes_for_error(RuntimeError("other")) == SLEEP_MINUTES
