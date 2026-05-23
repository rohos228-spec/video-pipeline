"""Тесты на app/telegram/keyboards/common.py (Phase E.4 step 2)."""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.telegram.callback_registry import CB, TG_CALLBACK_LIMIT
from app.telegram.keyboards.common import (
    btn_back_to_menu,
    btn_my_projects,
    btn_new_project,
    kb_back_to_main,
    kb_hitl_4buttons,
    kb_session_summary,
    kb_yes_no,
    make_callback,
    row_back_menu,
)

# ────────────────────────────── make_callback ───────────────────────────────


def test_make_callback_simple() -> None:
    assert make_callback(CB.PROJ_MENU, 42, "menu") == "proj:42:menu"
    assert make_callback(CB.AI_APPROVE, 99) == "ai:approve:99"
    assert make_callback(CB.MENU_NEW) == "menu:new"


def test_make_callback_string_int_mix() -> None:
    """Принимает и str и int."""
    assert make_callback(CB.MASS_SUB, 7, "rachki-cyberpunk") == "mass:sub:7:rachki-cyberpunk"


def test_make_callback_raises_on_overflow() -> None:
    """callback > 64 байт → ValueError, не молчаливое отрезание Telegram'ом."""
    with pytest.raises(ValueError, match="64"):
        make_callback(CB.PROJ_MENU, "x" * 80)


def test_make_callback_with_utf8() -> None:
    """UTF-8 байты считаются правильно (каждый рус-символ ≥ 2 байта)."""
    cb = make_callback(CB.MASS_TOPICS, "русский_slug")
    assert len(cb.encode("utf-8")) <= TG_CALLBACK_LIMIT


# ────────────────────────────── individual buttons ──────────────────────────


def test_btn_back_to_menu_uses_menu_root() -> None:
    btn = btn_back_to_menu()
    assert btn.callback_data == CB.MENU_ROOT.value
    assert "Меню" in btn.text or "меню" in btn.text


def test_btn_my_projects() -> None:
    assert btn_my_projects().callback_data == CB.MENU_LIST.value


def test_btn_new_project() -> None:
    assert btn_new_project().callback_data == CB.MENU_NEW.value


# ────────────────────────────── row_back_menu ───────────────────────────────


def test_row_back_menu_default_just_menu() -> None:
    row = row_back_menu()
    assert len(row) == 1
    assert row[0].callback_data == CB.MENU_ROOT.value


def test_row_back_menu_with_back_callback() -> None:
    row = row_back_menu(back_callback="proj:42:menu")
    assert len(row) == 2
    assert row[0].callback_data == "proj:42:menu"
    assert row[1].callback_data == CB.MENU_ROOT.value


def test_row_back_menu_skip_menu() -> None:
    row = row_back_menu(back_callback="proj:42:menu", include_menu=False)
    assert len(row) == 1
    assert row[0].callback_data == "proj:42:menu"


def test_row_back_menu_overflow() -> None:
    with pytest.raises(ValueError, match="64"):
        row_back_menu(back_callback="x" * 100)


# ────────────────────────────── kb_back_to_main ─────────────────────────────


def test_kb_back_to_main_single_button() -> None:
    kb = kb_back_to_main()
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 1


# ────────────────────────────── kb_yes_no ───────────────────────────────────


def test_kb_yes_no_basic() -> None:
    kb = kb_yes_no(yes_callback="confirm:42", no_callback="cancel:42")
    assert isinstance(kb, InlineKeyboardMarkup)
    assert kb.inline_keyboard[0][0].callback_data == "confirm:42"
    assert kb.inline_keyboard[0][1].callback_data == "cancel:42"


def test_kb_yes_no_custom_text() -> None:
    kb = kb_yes_no(
        yes_callback="x", no_callback="y",
        yes_text="🚀 Поехали", no_text="Стоп",
    )
    assert "Поехали" in kb.inline_keyboard[0][0].text
    assert kb.inline_keyboard[0][1].text == "Стоп"


def test_kb_yes_no_overflow_caught_at_build() -> None:
    with pytest.raises(ValueError, match="64"):
        kb_yes_no(yes_callback="x" * 100, no_callback="ok")


# ────────────────────────────── kb_hitl_4buttons ────────────────────────────


def test_kb_hitl_4buttons_layout() -> None:
    """AGENTS.md §10 инвариант: ровно 4 кнопки в 2 ряда."""
    kb = kb_hitl_4buttons(
        approve_cb="a", regen_cb="b", clarify_cb="c", reject_cb="d",
    )
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 2
    assert len(kb.inline_keyboard[0]) == 2
    assert len(kb.inline_keyboard[1]) == 2


def test_kb_hitl_4buttons_order() -> None:
    """Порядок: ✅ approve, 🔁 regen, ✏️ clarify, ❌ reject."""
    kb = kb_hitl_4buttons(
        approve_cb="A", regen_cb="R", clarify_cb="C", reject_cb="X",
    )
    btns = [b for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for b in btns]
    assert callbacks == ["A", "R", "C", "X"]
    # Emojis should match standard
    assert "✅" in btns[0].text
    assert "🔁" in btns[1].text
    assert "✏️" in btns[2].text
    assert "❌" in btns[3].text


def test_kb_hitl_4buttons_overflow_caught() -> None:
    with pytest.raises(ValueError, match="64"):
        kb_hitl_4buttons(
            approve_cb="x" * 100, regen_cb="r", clarify_cb="c", reject_cb="x",
        )


# ────────────────────────────── kb_session_summary ──────────────────────────


def test_kb_session_summary_layout() -> None:
    kb = kb_session_summary(cancel_callback="c", status_callback="s")
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 2


def test_kb_session_summary_ai_integration() -> None:
    """Реальная сборка для AI-агента — callbacks ≤ 64 байт."""
    kb = kb_session_summary(
        cancel_callback=make_callback(CB.AI_CANCEL, 999_999_999),
        status_callback=make_callback(CB.AI_STATUS, 999_999_999),
    )
    for row in kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data
            assert len(btn.callback_data.encode("utf-8")) <= TG_CALLBACK_LIMIT
