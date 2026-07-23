"""Contract: Studio Excel preview defaults to full sheet, not a 8–10 row focus window."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
XLSX = (REPO / "web" / "src" / "lib" / "xlsx-sheets.ts").read_text(encoding="utf-8")
STUDIO = (REPO / "web" / "src" / "components" / "studio" / "node-studio.tsx").read_text(
    encoding="utf-8"
)
VMENU = (REPO / "web" / "src" / "components" / "canvas" / "node-v-menu.tsx").read_text(
    encoding="utf-8"
)
VMENU_XLSX = (
    REPO / "web" / "src" / "components" / "canvas" / "node-v-menu-excel.tsx"
).read_text(encoding="utf-8")


def test_studio_defaults_to_full_sheet_params() -> None:
    assert "xlsxStudioPreviewParams" in XLSX
    assert "XLSX_STUDIO_MAX_ROWS = 500" in XLSX
    assert "XLSX_STUDIO_MAX_COLS = 120" in XLSX
    # Default path (no focusKeyRows) starts at row 1 with full caps
    assert "startRow: 1" in XLSX
    assert "maxRows: XLSX_STUDIO_MAX_ROWS" in XLSX


def test_studio_uses_excel_grid_not_false_500x200_banner() -> None:
    assert "StudioExcelGrid" in STUDIO
    assert "Показано до {XLSX_STUDIO_MAX_ROWS}" not in STUDIO
    assert "truncated_rows" in STUDIO


def test_studio_ui_uses_full_params_and_optional_focus_toggle() -> None:
    assert "xlsxStudioPreviewParams" in STUDIO
    assert "xlsxFocusKeyRows" in STUDIO
    assert "Только ключевые строки" in STUDIO
    # excel_gpt can open Excel tab (no longer redirected to settings-only)
    assert 'if (slot.kind === "excel") setTab("excel")' in STUDIO
    assert "isExcelGptNode(nodeType)" in STUDIO.split("const showExcel")[1][:400]


def test_vmenu_shows_excel_for_excel_gpt_and_larger_preview() -> None:
    assert "!isExcelGptNode(nodeType) && excelSlot" not in VMENU
    assert "excelSlot != null && projectId != null" in VMENU
    assert "XLSX_PREVIEW_MAX_ROWS = 80" in XLSX
    assert "XLSX_PREVIEW_MAX_COLS = 40" in XLSX
    assert "max-h-[min(40vh,320px)]" in VMENU_XLSX
    assert "excelRow" in VMENU_XLSX


def test_excel_gpt_default_sheet_is_plan() -> None:
    assert 'nodeType === "excel_gpt"' in XLSX
    assert "SHEET_PLAN_V8" in XLSX
