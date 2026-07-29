"""TSV/xlsx write-back из текстового ответа GPT API."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.services.xlsx_text_writeback import (
    apply_sheet_blocks_to_xlsx,
    extract_sheet_blocks,
    writeback_project_xlsx,
)


def test_extract_sheet_blocks_tsv() -> None:
    text = (
        "# Лист: план\n"
        "A\tB\tC\n"
        "1\t2\t3\n"
        "# Лист: Кадры\n"
        "n\ttext\n"
        "1\thello\n"
    )
    blocks = extract_sheet_blocks(text)
    assert list(blocks.keys()) == ["план", "Кадры"]
    assert blocks["план"][0] == ["A", "B", "C"]
    assert blocks["Кадры"][1] == ["1", "hello"]


def test_extract_from_fenced_block() -> None:
    text = "вот результат:\n```tsv\n# Лист: Данные\nx\ty\n10\t20\n```\nконец"
    blocks = extract_sheet_blocks(text)
    assert "Данные" in blocks
    assert blocks["Данные"] == [["x", "y"], ["10", "20"]]


def test_apply_and_writeback(tmp_path: Path) -> None:
    src = tmp_path / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "план"
    ws["A1"] = "old"
    ws["A5"] = "keep-me"
    wb.create_sheet("Кадры")
    wb.save(src)
    wb.close()

    reply = "# Лист: план\nname\tval\nfoo\tbar\n# Лист: Кадры\nid\n1\n"
    out = writeback_project_xlsx(
        project_xlsx=src,
        reply_text=reply,
        downloaded_paths=[],
    )
    assert out == src
    wb2 = load_workbook(src)
    assert wb2["план"]["A1"].value == "name"
    assert wb2["план"]["B2"].value == "bar"
    # Overlay: строки вне TSV не стираем
    assert wb2["план"]["A5"].value == "keep-me"
    assert wb2["Кадры"]["A2"].value == "1"
    wb2.close()


def test_apply_does_not_add_foreign_sheets_on_v8_workbook(tmp_path: Path) -> None:
    src = tmp_path / "project.xlsx"
    dest = tmp_path / "out.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Общий план"
    ws["A1"] = "label"
    wb.create_sheet("план")
    wb.save(src)
    wb.close()

    apply_sheet_blocks_to_xlsx(
        src,
        {"Общий план": [["Хук", "длинный текст"]], "ЧужойЛист": [["x"]]},
        dest,
    )
    wb2 = load_workbook(dest)
    assert "ЧужойЛист" not in wb2.sheetnames
    assert wb2["Общий план"]["A1"].value == "Хук"
    assert wb2["Общий план"]["B1"].value == "длинный текст"
    assert "план" in wb2.sheetnames
    wb2.close()


def test_writeback_prefers_downloaded_xlsx(tmp_path: Path) -> None:
    project = tmp_path / "project.xlsx"
    dl = tmp_path / "content_1.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = "from_download"
    wb.save(dl)
    wb.close()
    Workbook().save(project)

    out = writeback_project_xlsx(
        project_xlsx=project,
        reply_text="# Лист: X\na\tb\n",
        downloaded_paths=[dl],
    )
    assert out == project
    wb2 = load_workbook(project)
    assert wb2.active["A1"].value == "from_download"
    wb2.close()


def test_writeback_prose_fallback_into_general_plan(tmp_path: Path) -> None:
    from app.services.plan_validation import is_meaningful_general_plan
    from app.services.xlsx_v8_import import _read_general_plan

    src = tmp_path / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Общий план"
    ws["A1"] = "Хук"
    ws["A2"] = "Основная тема"
    ws.merge_cells("B2:D2")
    wb.create_sheet("план")
    wb.save(src)
    wb.close()

    prose = "А" * 250 + "\n\nРим был велик: армия, право, дороги."
    out = writeback_project_xlsx(
        project_xlsx=src,
        reply_text=prose,
        downloaded_paths=[],
    )
    assert out == src
    wb2 = load_workbook(src, data_only=True)
    # Структура строк не сломана: подпись A2 на месте, текст в B2
    assert wb2["Общий план"]["A1"].value == "Хук"
    assert wb2["Общий план"]["A2"].value == "Основная тема"
    assert "Рим был велик" in str(wb2["Общий план"]["B2"].value or "")
    text = _read_general_plan(wb2) or ""
    wb2.close()
    assert is_meaningful_general_plan(text)
    assert "Рим был велик" in text


def test_apply_creates_missing_sheet(tmp_path: Path) -> None:
    src = tmp_path / "a.xlsx"
    dest = tmp_path / "b.xlsx"
    Workbook().save(src)
    apply_sheet_blocks_to_xlsx(src, {"Новый": [["a", "b"]]}, dest)
    wb = load_workbook(dest)
    assert "Новый" in wb.sheetnames
    assert wb["Новый"]["B1"].value == "b"
    wb.close()
