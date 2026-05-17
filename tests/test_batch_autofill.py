"""Тесты `app/services/batch_autofill.py` — детекция пустых ячеек.

Round-trip к ChatGPT не покрываем — он требует реального браузера/CDP.
"""

from __future__ import annotations

from app.services.batch_autofill import has_empty_card_cells


def test_empty_when_only_titles():
    rows = [
        {"title": "T1"},
        {"title": "T2"},
    ]
    assert has_empty_card_cells(rows) is True


def test_not_empty_when_all_fields_filled():
    rows = [
        {
            "title": "T1", "source": "src", "style": "S",
            "hook_type": "H", "emotion": "E", "fact": "F",
            "logic": "L", "integration": "I", "shoot_note": "N",
            "video_duration_sec": 30,
        },
    ]
    assert has_empty_card_cells(rows) is False


def test_empty_when_one_row_partial():
    """Один ряд заполнен полностью, другой — частично → нужно GPT-заполнение."""
    rows = [
        {
            "title": "T1", "source": "src", "style": "S",
            "hook_type": "H", "emotion": "E", "fact": "F",
            "logic": "L", "integration": "I", "shoot_note": "N",
            "video_duration_sec": 30,
        },
        {"title": "T2", "style": "Только стиль"},  # пробелы в остальных
    ]
    assert has_empty_card_cells(rows) is True


def test_skip_rows_without_title():
    """Строки без названия игнорируем — это не темы вообще."""
    rows = [
        {"title": ""},
        {"title": None},
    ]
    assert has_empty_card_cells(rows) is False


def test_empty_strings_count_as_empty():
    """Поле = пустая строка должно считаться пустым."""
    rows = [
        {"title": "T", "style": "", "hook_type": "  "},
    ]
    assert has_empty_card_cells(rows) is True


def test_voiceover_chars_does_not_block():
    """Поле voiceover_chars_target — это формула в Excel, его пустота не
    должна заставлять запускать GPT-автозаполнение (формула считается
    Excel'ом из L).
    """
    rows = [
        {
            "title": "T1", "source": "src", "style": "S",
            "hook_type": "H", "emotion": "E", "fact": "F",
            "logic": "L", "integration": "I", "shoot_note": "N",
            "video_duration_sec": 30,
            # voiceover_chars_target пропущен — это нормально, формула.
        },
    ]
    assert has_empty_card_cells(rows) is False
