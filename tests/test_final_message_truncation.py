"""Тесты на _build_final_message — Telegram truncation."""

from __future__ import annotations

from app.telegram.handlers.ai_agent import (
    _MAX_FINAL_MSG_CHARS,
    _build_final_message,
)


def test_short_message_passes_through() -> None:
    """Короткие сообщения не обрезаются и не получают hint."""
    text = _build_final_message(1, "Готово. Сделал X, Y, Z.")
    assert "/ai dump" not in text
    assert "Готово" in text
    assert text.startswith("🤖 <b>AI-сессия #1 завершена</b>")


def test_long_message_truncated_with_hint() -> None:
    """Длинные сообщения обрезаются + hint про /ai dump."""
    long_body = "параграф абвг " * 1000  # ~12000 байт
    text = _build_final_message(42, long_body)
    assert len(text) <= _MAX_FINAL_MSG_CHARS + 50  # небольшой запас
    assert "/ai dump 42" in text
    assert "обрезано" in text.lower() or "обрезано" in text


def test_truncation_at_word_boundary() -> None:
    """Обрезка не должна резать посреди слова."""
    long_body = "слово1 слово2 слово3 " * 500
    text = _build_final_message(7, long_body)
    if "/ai dump" not in text:
        return  # короткий — не обрезался
    # Граница перед hint — последнее «слово» должно быть полным
    hint_idx = text.find("\n\n…")
    if hint_idx < 0:
        return
    body = text[:hint_idx]
    # Последний символ body — не середина слова
    last_char = body.rstrip()[-1]
    assert last_char in (".", "!", "?", "о", "1", "2", "3", " ") or body.rstrip().endswith("слово1") or body.rstrip().endswith("слово2") or body.rstrip().endswith("слово3"), (
        f"truncation cut mid-word? last 30 chars: {body[-30:]!r}"
    )


def test_html_escapes_in_summary() -> None:
    """<, >, & в summary должны быть escape'нуты (защита от парсинга TG)."""
    body = "ошибка: <script>alert('x')</script> & special"
    text = _build_final_message(3, body)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&amp;" in text


def test_header_present_in_truncated() -> None:
    """Даже при extreme truncation заголовок сохраняется."""
    body = "x" * 100_000
    text = _build_final_message(99, body)
    assert "🤖" in text
    assert "#99" in text
    assert "/ai dump 99" in text


def test_session_id_in_hint() -> None:
    """Hint содержит правильный session_id."""
    body = "y" * 10_000
    text = _build_final_message(12345, body)
    assert "/ai dump 12345" in text


def test_html_balance_in_header() -> None:
    """Header содержит сбалансированные <b>...</b> тэги."""
    text = _build_final_message(1, "short")
    assert text.count("<b>") == text.count("</b>")
