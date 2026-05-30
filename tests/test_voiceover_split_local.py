"""voiceover_split_local: fallback разбивка без GPT в xlsx."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.services.voiceover_split_local import (
    parse_dash_separated_blocks,
    split_voiceover_locally,
    write_voiceover_blocks_to_xlsx,
)
from app.services.xlsx_v8_import import ROW_VOICEOVER_V8, SHEET_PLAN_V8
from app.services import xlsx_step_runners as xsr


def test_parse_dash_blocks() -> None:
    text = "Первый блок текста.\n-\nВторой блок текста.\n-\nТретий блок."
    blocks = parse_dash_separated_blocks(text)
    assert len(blocks) == 3


def test_write_blocks_to_xlsx(tmp_path: Path) -> None:
    p = tmp_path / "project.xlsx"
    wb = Workbook()
    wb.active.title = SHEET_PLAN_V8
    wb.save(p)
    n = write_voiceover_blocks_to_xlsx(p, ["alpha block", "beta block"])
    assert n == 2
    assert xsr._count_v8_voiceover_blocks(p) == 2


def test_split_voiceover_locally() -> None:
    text = (
        "Первое предложение достаточно длинное для теста. "
        "Второе предложение тоже подходит. "
        "Третье завершает мысль."
    )
    blocks = split_voiceover_locally(text)
    assert len(blocks) >= 2
