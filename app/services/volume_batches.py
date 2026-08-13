"""Адаптивные батчи, когда нода вернула только часть результата.

Считаем: объём ответа превышен. ``delivered`` = сколько единиц
успешно пришло в первом (частичном) ответе.

- остаток ≤ delivered → один доборный запрос на весь остаток;
- остаток > delivered → нарезать пачками ≈ ``ratio`` (по умолчанию 80%)
  от ``delivered``.
"""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")

DEFAULT_VOLUME_RATIO = 0.8


def inferred_batch_size(
    delivered: int, *, ratio: float = DEFAULT_VOLUME_RATIO
) -> int:
    """Размер следующих пачек ≈ ratio от того, что реально доехало."""
    if delivered < 1:
        return 1
    r = float(ratio) if ratio and ratio > 0 else DEFAULT_VOLUME_RATIO
    return max(1, int(delivered * r))


def plan_remainder_batches(
    remaining: Sequence[T],
    *,
    delivered: int,
    ratio: float = DEFAULT_VOLUME_RATIO,
) -> list[list[T]]:
    """План добора после частичного ответа ноды.

    ``remaining`` — единицы, которые ещё не покрыты этим запросом.
    """
    items = list(remaining)
    if not items:
        return []
    if delivered < 1:
        # Нет сигнала по ёмкости — один повтор всего остатка (решает caller).
        return [items]
    if len(items) <= delivered:
        return [items]
    size = inferred_batch_size(delivered, ratio=ratio)
    return [items[i : i + size] for i in range(0, len(items), size)]


def rechunk_tail(
    tail: Sequence[T],
    *,
    size: int,
) -> list[list[T]]:
    """Перенарезать ещё не отправленный хвост под новый размер пачки."""
    items = list(tail)
    n = max(1, int(size))
    if not items:
        return []
    return [items[i : i + n] for i in range(0, len(items), n)]
