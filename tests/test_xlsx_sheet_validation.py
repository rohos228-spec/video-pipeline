"""Проверка листов xlsx против шаблона."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.services.xlsx_v8_import import SHEET_GENERAL_V8, SHEET_PLAN_V8
from app.services.xlsx_versioning import validate_xlsx, validate_xlsx_sheets
from app.storage.project_sheet import SHEET_FRAMES, SHEET_GENERAL


def _save_v8(path: Path) -> None:
    wb = Workbook()
    wb.active.title = SHEET_PLAN_V8
    wb.create_sheet(SHEET_GENERAL_V8)
    wb.save(path)


def test_validate_xlsx_sheets_accepts_v8_layout(tmp_path: Path) -> None:
    p = tmp_path / "ok.xlsx"
    _save_v8(p)
    assert validate_xlsx_sheets(p) is None
    assert validate_xlsx(p) is None


def test_validate_xlsx_sheets_rejects_wrong_layout(tmp_path: Path) -> None:
    p = tmp_path / "bad.xlsx"
    wb = Workbook()
    wb.active.title = "Лист1"
    wb.create_sheet("Лист2")
    wb.save(p)
    err = validate_xlsx_sheets(p)
    assert err is not None
    assert "ошибка формата эксель таблицы" in err


def test_validate_xlsx_sheets_accepts_v7_layout(tmp_path: Path) -> None:
    p = tmp_path / "v7.xlsx"
    wb = Workbook()
    wb.active.title = SHEET_FRAMES
    wb.create_sheet(SHEET_GENERAL)
    wb.save(p)
    assert validate_xlsx_sheets(p) is None
