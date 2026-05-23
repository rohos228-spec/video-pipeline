"""Тесты на scripts/cb_inventory.py."""

from __future__ import annotations

from scripts.cb_inventory import _scan, render_markdown


def test_scan_finds_callbacks_and_handlers() -> None:
    data = _scan()
    assert "callbacks_by_prefix" in data
    assert "handlers_by_prefix" in data
    assert data["total_callbacks"] >= 100


def test_scan_resolves_to_cb_constants() -> None:
    """Каждый prefix должен быть resolved до CB-имени, не <UNKNOWN>."""
    data = _scan()
    unknown = [k for k in data["callbacks_by_prefix"] if k.startswith("<UNKNOWN")]
    assert not unknown, f"unregistered prefixes: {unknown}"


def test_render_markdown_includes_known_cbs() -> None:
    data = _scan()
    md = render_markdown(data)
    # Должны быть упомянуты популярные CB
    assert "CB.MENU_NEW" in md
    assert "CB.AI_APPROVE" in md
    assert "CB.HITL" in md
    assert "CB.PROJ_MENU" in md


def test_render_markdown_marks_dead_buttons() -> None:
    """Кнопки без handler'ов помечаются ⚠️."""
    data = _scan()
    md = render_markdown(data)
    # На текущем repo есть hero_* кнопки без явных F.data handlers
    # (они обработаны где-то ещё), markdown должен это пометить
    if data["callbacks_by_prefix"].get("HERO_COUNT") and not data[
        "handlers_by_prefix"
    ].get("HERO_COUNT"):
        assert "Нет handler" in md


def test_inventory_contains_used_callback_count() -> None:
    data = _scan()
    md = render_markdown(data)
    assert "CB-префиксов" in md
    assert "callback_data в коде" in md
