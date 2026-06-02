"""Tests for step_failure_policy counters (9 fails = 3 cycles × 3)."""

from app.services.step_failure_policy import (
    FAILS_PER_CYCLE,
    MAX_CYCLES,
    MAX_TOTAL_FAILS,
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
