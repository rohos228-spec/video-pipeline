"""Неблокирующее: отладочный тернарник снят, Excel остаётся в inline-меню."""

from pathlib import Path


def test_inline_menu_keeps_excel_without_debug_ternary() -> None:
    src = Path("web/src/components/studio/node-studio.tsx").read_text(encoding="utf-8")
    assert "{true ?" not in src
    inline = src.split("return createPortal(", 1)[1]
    assert "ExcelGptSettingsPanel" in inline
    assert "Скачать Excel" in inline or "excel" in inline.lower()
