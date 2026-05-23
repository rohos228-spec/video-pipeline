"""Тесты на расширенные фабрики keyboards/ (main_menu, project_menu, hitl_buttons)."""

from __future__ import annotations

import pytest

from app.telegram.callback_registry import CB, TG_CALLBACK_LIMIT
from app.telegram.keyboards.hitl_buttons import (
    kb_hitl_image,
    kb_hitl_video,
    parse_hitl_callback,
)
from app.telegram.keyboards.main_menu import (
    kb_main_menu,
    kb_mass_pause_resume,
)
from app.telegram.keyboards.project_menu import (
    kb_project_delete_confirm,
    kb_project_menu,
    kb_reset_step_confirm,
)

# ────────────────────────────── main_menu ───────────────────────────────────


def test_main_menu_basic_layout() -> None:
    kb = kb_main_menu()
    assert kb.inline_keyboard, "should have rows"
    # Все callbacks ≤ 64 байт
    for row in kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data
            assert len(btn.callback_data.encode("utf-8")) <= TG_CALLBACK_LIMIT


def test_main_menu_includes_new_and_list() -> None:
    kb = kb_main_menu()
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert CB.MENU_NEW.value in callbacks
    assert CB.MENU_LIST.value in callbacks
    assert CB.MASS_LIST.value in callbacks


def test_main_menu_hides_ai_when_disabled() -> None:
    kb = kb_main_menu(show_ai_agent=False, show_debug=False)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("ИИ-агент" in t for t in texts)
    assert not any("Debug" in t or "debug" in t for t in texts)


def test_mass_pause_resume_toggle() -> None:
    kb_paused = kb_mass_pause_resume(paused=True)
    assert "Возобновить" in kb_paused.inline_keyboard[0][0].text
    assert kb_paused.inline_keyboard[0][0].callback_data == CB.MENU_MASS_RESUME.value

    kb_running = kb_mass_pause_resume(paused=False)
    assert "Пауза" in kb_running.inline_keyboard[0][0].text
    assert kb_running.inline_keyboard[0][0].callback_data == CB.MENU_MASS_PAUSE.value


# ────────────────────────────── project_menu ────────────────────────────────


def test_project_menu_full_buttons() -> None:
    kb = kb_project_menu(
        42,
        current_step="generating_images",
        can_run=True,
        can_stop=True,
        can_excel=True,
    )
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Запустить" in t for t in texts)
    assert any("Прогнать" in t for t in texts)
    assert any("Остановить" in t for t in texts)
    assert any("Excel" in t for t in texts)


def test_project_menu_has_back_to_root() -> None:
    """AGENTS.md §10: «В меню» на каждом экране."""
    kb = kb_project_menu(42, current_step="plan")
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert CB.MENU_ROOT.value in callbacks


def test_project_menu_callbacks_within_limit() -> None:
    """Очень большие project_id + длинные step_code → ≤ 64 байт."""
    kb = kb_project_menu(
        999_999_999_999,
        current_step="generating_animation_prompts",
        can_run=True,
        can_stop=True,
        can_excel=True,
    )
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                assert len(btn.callback_data.encode("utf-8")) <= TG_CALLBACK_LIMIT


def test_project_delete_confirm() -> None:
    kb = kb_project_delete_confirm(42)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 2
    assert any("Удалить" in b.text for b in flat)
    assert any("Отмена" in b.text for b in flat)


def test_reset_step_confirm() -> None:
    kb = kb_reset_step_confirm(42, "plan")
    flat = [b for row in kb.inline_keyboard for b in row]
    assert any("Да" in b.text and "заново" in b.text for b in flat)
    assert any("Отмена" in b.text for b in flat)


# ────────────────────────────── hitl_buttons ────────────────────────────────


def test_hitl_image_4_buttons_invariant() -> None:
    """AGENTS.md §10: HITL для image ровно ✅/🔁/✏️/❌."""
    kb = kb_hitl_image(99, allow_edit_prompt=True, allow_original=False)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("✅" in t and "Одобрить" in t for t in texts)
    assert any("🔁" in t and "ерегенерир" in t for t in texts)  # Перегенер
    assert any("✏️" in t for t in texts)
    assert any("❌" in t for t in texts)


def test_hitl_image_no_edit_prompt() -> None:
    kb = kb_hitl_image(99, allow_edit_prompt=False)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Изменить промт" in t for t in texts)
    # Но Одобрить / Перегенер / Отклонить остаются
    assert any("Одобрить" in t for t in texts)
    assert any("Отклонить" in t for t in texts)


def test_hitl_image_with_original() -> None:
    kb = kb_hitl_image(99, allow_original=True)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Оригинал" in t for t in texts)


def test_hitl_video_3_buttons_no_edit() -> None:
    """Видео HITL без 'Изменить промт' (видео-промты не редактируем вручную)."""
    kb = kb_hitl_video(99)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Одобрить" in t for t in texts)
    assert any("Перегенер" in t for t in texts)
    assert any("Отклонить" in t for t in texts)
    assert not any("Изменить" in t for t in texts)


def test_hitl_callbacks_use_hitl_prefix() -> None:
    """Все callback_data hitl-кнопок начинаются с CB.HITL."""
    for factory in [
        lambda: kb_hitl_image(42, allow_edit_prompt=True, allow_original=True),
        lambda: kb_hitl_video(42),
    ]:
        kb = factory()
        for row in kb.inline_keyboard:
            for btn in row:
                assert btn.callback_data
                assert btn.callback_data.startswith(CB.HITL.value + ":")


def test_hitl_callbacks_within_limit() -> None:
    kb = kb_hitl_image(999_999_999_999, allow_edit_prompt=True, allow_original=True)
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                assert len(btn.callback_data.encode("utf-8")) <= TG_CALLBACK_LIMIT


# ────────────────────────────── parse_hitl_callback ─────────────────────────


@pytest.mark.parametrize(
    "data,expected",
    [
        ("hitl:42:approve", (42, "approve")),
        ("hitl:1:reject", (1, "reject")),
        ("hitl:99:regen", (99, "regen")),
        ("hitl:7:edit", (7, "edit")),
    ],
)
def test_parse_hitl_callback_valid(data: str, expected: tuple[int, str]) -> None:
    assert parse_hitl_callback(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        "",
        "foo:42:approve",
        "hitl",
        "hitl:approve",
        "hitl:notanint:approve",
        "ai:approve:42",  # wrong prefix
    ],
)
def test_parse_hitl_callback_invalid(data: str) -> None:
    assert parse_hitl_callback(data) is None


def test_parse_hitl_callback_handles_extra_segments() -> None:
    """`hitl:42:approve:extra` → берёт только первые 3 сегмента."""
    result = parse_hitl_callback("hitl:42:approve:extra:more")
    assert result == (42, "approve")
