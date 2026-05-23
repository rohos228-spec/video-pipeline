"""Consistency: callback_data в services/hitl.py == hitl:* по правилам CB.

`services/hitl.py:_keyboard()` собирает HITL-карточку с callback'ами
вида `hitl:{id}:{action}`. Параллельно `app/telegram/keyboards/hitl_buttons.py:
kb_hitl_image()` делает то же самое.

Этот тест проверяет:
1. Все callback'и из _keyboard() матчат CB.HITL (через is_registered).
2. Действия (approve, regen, edit, original, reject) одинаковые в обоих
   местах.
3. Когда придёт миграция services/hitl.py на kb_hitl_image — этот тест
   защитит от случайного breakage.
"""

from __future__ import annotations

from app.telegram.callback_registry import CB, is_registered
from app.telegram.keyboards.hitl_buttons import (
    kb_hitl_image,
    parse_hitl_callback,
)


def _extract_callbacks(kb_markup) -> list[str]:
    return [b.callback_data for row in kb_markup.inline_keyboard for b in row if b.callback_data]


def test_services_hitl_keyboard_callbacks_match_cb() -> None:
    """app/services/hitl.py:_keyboard() выдаёт callback'и которые покрыты CB."""
    from app.services.hitl import _keyboard

    kb = _keyboard(42, allow_edit=True, allow_original=True)
    cbs = _extract_callbacks(kb)
    for cb in cbs:
        assert is_registered(cb), (
            f"callback {cb!r} из services/hitl.py не зарегистрирован в CB"
        )


def test_services_hitl_keyboard_uses_hitl_prefix() -> None:
    """Все callback'и услуги начинаются с CB.HITL."""
    from app.services.hitl import _keyboard

    for allow_edit, allow_orig in [(False, False), (True, False), (False, True), (True, True)]:
        kb = _keyboard(7, allow_edit=allow_edit, allow_original=allow_orig)
        cbs = _extract_callbacks(kb)
        assert cbs, f"keyboard returned no callbacks for edit={allow_edit} orig={allow_orig}"
        for cb in cbs:
            assert cb.startswith(CB.HITL.value + ":"), (
                f"callback {cb!r} не начинается с CB.HITL"
            )


def test_actions_match_between_services_and_keyboards_module() -> None:
    """Action labels (approve/regen/edit/original/reject) общие в обоих местах."""
    from app.services.hitl import _keyboard

    legacy = _keyboard(42, allow_edit=True, allow_original=True)
    new = kb_hitl_image(42, allow_edit_prompt=True, allow_original=True)

    legacy_actions = set()
    for cb in _extract_callbacks(legacy):
        parsed = parse_hitl_callback(cb)
        if parsed:
            legacy_actions.add(parsed[1])

    new_actions = set()
    for cb in _extract_callbacks(new):
        parsed = parse_hitl_callback(cb)
        if parsed:
            new_actions.add(parsed[1])

    # Оба варианта поддерживают approve, regen, reject
    common = legacy_actions & new_actions
    for action in ("approve", "regen", "reject"):
        assert action in common, (
            f"action {action!r} расходится: legacy={legacy_actions}, new={new_actions}"
        )

    # При allow_edit/allow_original — оба варианта должны поддерживать edit и original
    assert "edit" in legacy_actions and "edit" in new_actions
    assert "original" in legacy_actions and "original" in new_actions


def test_parse_hitl_callback_works_on_services_output() -> None:
    """parse_hitl_callback корректно разбирает то что выдаёт services/hitl.py."""
    from app.services.hitl import _keyboard

    kb = _keyboard(99, allow_edit=True, allow_original=True)
    cbs = _extract_callbacks(kb)
    for cb in cbs:
        parsed = parse_hitl_callback(cb)
        assert parsed is not None, f"can't parse {cb!r}"
        hitl_id, action = parsed
        assert hitl_id == 99
        assert action in {"approve", "regen", "edit", "original", "reject"}


def test_hitl_id_is_int_not_string() -> None:
    """services/hitl.py использует {hitl_id} как int — проверим что parse возвращает int."""
    from app.services.hitl import _keyboard

    kb = _keyboard(123456, allow_edit=False, allow_original=False)
    cbs = _extract_callbacks(kb)
    for cb in cbs:
        parsed = parse_hitl_callback(cb)
        assert parsed is not None
        hitl_id, _ = parsed
        assert isinstance(hitl_id, int)
        assert hitl_id == 123456
