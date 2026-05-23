"""Тесты на app/telegram/keyboards/wizard.py."""

from __future__ import annotations

import pytest

from app.telegram.callback_registry import CB, TG_CALLBACK_LIMIT
from app.telegram.keyboards.wizard import (
    kb_wizard_choice,
    kb_wizard_confirm,
    kb_wizard_start,
)


def _flatten_callbacks(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]


# ────────────────────────────── kb_wizard_start ─────────────────────────────


def test_wizard_start_has_two_options() -> None:
    kb = kb_wizard_start(42)
    cbs = _flatten_callbacks(kb)
    assert len(cbs) == 2
    assert any(":start" in c for c in cbs)
    assert any(":reset" in c for c in cbs)


def test_wizard_start_uses_wiz_prefix() -> None:
    kb = kb_wizard_start(99)
    for cb in _flatten_callbacks(kb):
        assert cb.startswith(CB.WIZ.value + ":")


# ────────────────────────────── kb_wizard_choice ────────────────────────────


def test_wizard_choice_options() -> None:
    kb = kb_wizard_choice(
        42,
        "video_generator",
        [("Veo 3 Fast", "veo_3_fast"), ("Kling 2.6", "kling_2_6")],
    )
    cbs = _flatten_callbacks(kb)
    # 2 опции + 1 кнопка отмены
    assert len(cbs) == 3
    assert any("veo_3_fast" in c for c in cbs)
    assert any("kling_2_6" in c for c in cbs)


def test_wizard_choice_with_back() -> None:
    kb = kb_wizard_choice(
        42, "x", [("a", "av")], back_callback="wiz:42:prev"
    )
    nav_row = kb.inline_keyboard[-1]
    nav_cbs = [b.callback_data for b in nav_row]
    assert any("prev" in c for c in nav_cbs)
    assert any("cancel" in c for c in nav_cbs)


def test_wizard_choice_no_cancel() -> None:
    kb = kb_wizard_choice(
        42, "x", [("a", "av")], add_cancel=False
    )
    cbs = _flatten_callbacks(kb)
    # Только 1 опция, нет cancel/back
    assert len(cbs) == 1
    assert "av" in cbs[0]


def test_wizard_choice_callback_under_limit() -> None:
    """Разумно длинные batch_id + field/value → callback ≤ 64 байт.

    Wizard собирает callback как 'wiz:{batch_id}:set:{field}:{value}',
    минимум 9 байт overhead. Под лимит 64 байта помещается batch_id до
    12 цифр + field+value совокупно ≤ ~40 байт.
    """
    kb = kb_wizard_choice(
        99_999,
        "video_gen",
        [("Veo 3 Fast", "veo_3_fast"), ("Kling 2.6", "kling_2_6")],
    )
    for cb in _flatten_callbacks(kb):
        assert len(cb.encode("utf-8")) <= TG_CALLBACK_LIMIT




def test_wizard_choice_overflow_raises() -> None:
    """Очень длинный value → make_callback() кидает ValueError на этапе
    сборки клавиатуры (защита от Telegram BadRequest в проде).
    """
    with pytest.raises(ValueError, match="64"):
        kb_wizard_choice(
            999_999_999_999,
            "very_long_field_name",
            [("X", "some_very_long_value_string_definitely_overflow")],
        )


# ────────────────────────────── kb_wizard_confirm ───────────────────────────


def test_wizard_confirm_layout() -> None:
    kb = kb_wizard_confirm(42)
    assert len(kb.inline_keyboard) == 1
    btns = kb.inline_keyboard[0]
    assert len(btns) == 2
    assert any("Применить" in b.text for b in btns)
    assert any("Отмена" in b.text or "↩" in b.text for b in btns)


def test_wizard_all_callbacks_in_cb_registry() -> None:
    """Все callback'и wizard-фабрик используют CB.WIZ префикс."""
    factories = [
        lambda: kb_wizard_start(42),
        lambda: kb_wizard_choice(42, "x", [("a", "b")]),
        lambda: kb_wizard_confirm(42),
    ]
    for factory in factories:
        kb = factory()
        for cb in _flatten_callbacks(kb):
            assert cb.startswith(CB.WIZ.value + ":") or cb.startswith(CB.WIZ.value)
