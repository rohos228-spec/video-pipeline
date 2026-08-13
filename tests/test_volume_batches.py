"""volume_batches: добор / нарезка ~80% после частичного ответа."""

from __future__ import annotations

from app.services.volume_batches import (
    inferred_batch_size,
    plan_remainder_batches,
    rechunk_tail,
)


def test_inferred_batch_size_80pct() -> None:
    assert inferred_batch_size(10) == 8
    assert inferred_batch_size(25) == 20
    assert inferred_batch_size(1) == 1
    assert inferred_batch_size(0) == 1


def test_plan_remainder_smaller_than_delivered_one_batch() -> None:
    # выдали 15, осталось 10 → один добор
    parts = plan_remainder_batches(list(range(10)), delivered=15)
    assert parts == [list(range(10))]


def test_plan_remainder_larger_splits_at_80pct() -> None:
    # выдали 10, осталось 25 → пачки по 8
    parts = plan_remainder_batches(list(range(25)), delivered=10)
    assert [len(p) for p in parts] == [8, 8, 8, 1]
    assert [x for p in parts for x in p] == list(range(25))


def test_plan_remainder_empty() -> None:
    assert plan_remainder_batches([], delivered=5) == []


def test_rechunk_tail() -> None:
    assert rechunk_tail(list(range(10)), size=4) == [
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [8, 9],
    ]
