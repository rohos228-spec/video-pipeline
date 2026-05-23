"""Тесты на scripts/migrate_callback_to_cb.py — миграция callback'ов в CB."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from scripts.migrate_callback_to_cb import (
    _analyze_string_template,
    _find_cb_for_prefix,
    _find_replacements,
    _format_replacement,
    _process_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ────────────────────────────── _find_cb_for_prefix ─────────────────────────


@pytest.mark.parametrize(
    "prefix,expected_name",
    [
        ("ai:approve", "AI_APPROVE"),
        ("menu:new", "MENU_NEW"),
        ("menu:list", "MENU_LIST"),
        ("mass:start", "MASS_START"),
        ("proj", "PROJ_MENU"),
        ("hitl", "HITL"),
    ],
)
def test_find_cb_for_prefix_exact(prefix: str, expected_name: str) -> None:
    assert _find_cb_for_prefix(prefix) == expected_name


def test_find_cb_for_prefix_longest_match() -> None:
    """proj:42:menu → matched by CB.PROJ_MENU (value='proj')."""
    # CB.PROJ_MENU.value = 'proj', потому что в нашем enum это базовый префикс.
    # Этот тест проверяет что longest match работает.
    assert _find_cb_for_prefix("ai:approve:42:extra") == "AI_APPROVE"


def test_find_cb_for_prefix_not_found() -> None:
    assert _find_cb_for_prefix("totally_unknown:foo:bar") is None
    assert _find_cb_for_prefix("") is None


# ────────────────────────────── _analyze_string_template ────────────────────


def test_analyze_template_simple() -> None:
    res = _analyze_string_template("menu:new")
    assert res == ("MENU_NEW", [])


def test_analyze_template_with_args() -> None:
    res = _analyze_string_template("ai:approve:{tc_id}")
    assert res is not None
    cb_name, parts = res
    assert cb_name == "AI_APPROVE"
    assert parts == ["{tc_id}"]


def test_analyze_template_compound_args() -> None:
    res = _analyze_string_template("proj:{pid}:menu")
    assert res is not None
    cb_name, parts = res
    assert cb_name == "PROJ_MENU"
    assert parts == ["{pid}", "menu"]


def test_analyze_template_not_in_cb() -> None:
    assert _analyze_string_template("zzz_unknown:foo") is None
    assert _analyze_string_template("") is None


# ────────────────────────────── _format_replacement ─────────────────────────


def test_format_no_args() -> None:
    assert _format_replacement("menu:new", "MENU_NEW", []) == "make_callback(CB.MENU_NEW)"


def test_format_with_var() -> None:
    out = _format_replacement(
        "ai:approve:{tc_id}", "AI_APPROVE", ["{tc_id}"]
    )
    assert out == "make_callback(CB.AI_APPROVE, tc_id)"


def test_format_with_var_and_literal() -> None:
    out = _format_replacement(
        "proj:{pid}:menu", "PROJ_MENU", ["{pid}", "menu"]
    )
    assert out == "make_callback(CB.PROJ_MENU, pid, 'menu')"


# ────────────────────────────── _find_replacements ──────────────────────────


def test_find_replacements_constant_string() -> None:
    code = '''
from aiogram.types import InlineKeyboardButton

btn = InlineKeyboardButton(text="Меню", callback_data="menu:new")
'''
    repls = _find_replacements(code)
    assert len(repls) == 1
    assert repls[0].template == "menu:new"
    assert repls[0].cb_name == "MENU_NEW"


def test_find_replacements_fstring() -> None:
    code = '''
from aiogram.types import InlineKeyboardButton

pid = 7
btn = InlineKeyboardButton(text="Open", callback_data=f"proj:{pid}:menu")
'''
    repls = _find_replacements(code)
    assert len(repls) == 1
    assert repls[0].cb_name == "PROJ_MENU"
    assert "{pid}" in repls[0].template


def test_find_replacements_skips_already_migrated() -> None:
    """Если строка уже содержит make_callback — пропускаем."""
    code = '''
from aiogram.types import InlineKeyboardButton
from app.telegram.keyboards import make_callback
from app.telegram.callback_registry import CB

btn = InlineKeyboardButton(text="x", callback_data=make_callback(CB.MENU_NEW))
'''
    repls = _find_replacements(code)
    # Не найдёт ничего — это уже мигрировано
    assert all("make_callback" not in r.original for r in repls)


def test_find_replacements_unknown_prefix_creates_todo() -> None:
    code = '''
from aiogram.types import InlineKeyboardButton

btn = InlineKeyboardButton(text="x", callback_data="totally:unknown")
'''
    repls = _find_replacements(code)
    assert len(repls) == 1
    assert repls[0].cb_name is None
    assert "TODO" in repls[0].suggested


# ────────────────────────────── _process_file (dry-run) ─────────────────────


def test_process_file_dry_run() -> None:
    """Dry-run не должен модифицировать файл."""
    code = '''
from aiogram.types import InlineKeyboardButton

btn = InlineKeyboardButton(text="x", callback_data="menu:new")
'''
    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix="_dry.tmp.py", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = Path(tmp.name)
    try:
        n = _process_file(tmp_path, apply=False, verbose=False)
        assert n == 1
        assert tmp_path.read_text() == code  # не изменён
    finally:
        tmp_path.unlink(missing_ok=True)


def test_process_file_apply_rewrites_literal() -> None:
    """С --apply переписывает string-литералы."""
    code = '''
from aiogram.types import InlineKeyboardButton

btn = InlineKeyboardButton(text="x", callback_data="menu:new")
'''
    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix="_apply.tmp.py", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = Path(tmp.name)
    try:
        _process_file(tmp_path, apply=True, verbose=False)
        new_code = tmp_path.read_text()
        assert 'callback_data="menu:new"' not in new_code
        assert "make_callback(CB.MENU_NEW)" in new_code
    finally:
        tmp_path.unlink(missing_ok=True)


def test_process_file_does_not_rewrite_fstring_automatically() -> None:
    """f-string'и пропускаем — слишком много edge cases."""
    code = '''
from aiogram.types import InlineKeyboardButton

pid = 5
btn = InlineKeyboardButton(text="x", callback_data=f"proj:{pid}:menu")
'''
    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix="_fstr.tmp.py", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = Path(tmp.name)
    try:
        n = _process_file(tmp_path, apply=True, verbose=False)
        assert n >= 1
        # f-string должен остаться нетронутым
        new_code = tmp_path.read_text()
        assert 'f"proj:{pid}:menu"' in new_code
    finally:
        tmp_path.unlink(missing_ok=True)


# ────────────────────────────── repo-wide audit ─────────────────────────────


def test_repo_audit_finds_many() -> None:
    """Sanity: скрипт находит достаточно кандидатов в реальном коде."""
    total = 0
    for py in (REPO_ROOT / "app" / "telegram").rglob("*.py"):
        if "__pycache__" in py.parts or "test_" in py.name:
            continue
        if py.name in ("ai_agent.py", "debug.py"):
            # Уже мигрированы — там 0
            continue
        repls = _find_replacements(py.read_text(encoding="utf-8"))
        total += len(repls)
    # Минимум 50 кандидатов в app/telegram/ (без ai_agent.py / debug.py)
    assert total >= 50, f"only {total} candidates found?"
