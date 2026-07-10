"""Проверка, что «общий план» — реальный GPT-результат, а не пустой шаблон."""

from __future__ import annotations

MIN_GENERAL_PLAN_CHARS = 200


def is_meaningful_general_plan(text: str | None) -> bool:
    """True если в general_plan достаточно содержимого (как sync_after_plan)."""
    return bool(text and len(text.strip()) >= MIN_GENERAL_PLAN_CHARS)
