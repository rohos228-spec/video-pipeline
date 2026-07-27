"""Формат времени результата Create: всегда «N мин M сек»."""

from __future__ import annotations

from app.services.generation_storage import format_elapsed_min_sec


def test_format_elapsed_always_min_and_sec() -> None:
    assert format_elapsed_min_sec(0) == "0 мин 0 сек"
    assert format_elapsed_min_sec(45) == "0 мин 45 сек"
    assert format_elapsed_min_sec(60) == "1 мин 0 сек"
    assert format_elapsed_min_sec(125) == "2 мин 5 сек"
    assert format_elapsed_min_sec(None) == "0 мин 0 сек"
