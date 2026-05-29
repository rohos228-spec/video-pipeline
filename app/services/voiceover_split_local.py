"""Локальная разбивка voiceover → xlsx (fallback если GPT не записал R49).

GPT часто кладёт блоки в текст ответа (через «-»), а xlsx возвращает
без изменений — особенно со старым default.md. Тогда пишем блоки сами.
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger
from openpyxl import load_workbook

from app.services.xlsx_v8_import import (
    ROW_VOICEOVER_V8,
    SHEET_PLAN_V8,
    _resolve_plan_sheet,
)

_MIN_BLOCK_LEN = 8


def parse_dash_separated_blocks(text: str) -> list[str]:
    """Блоки из ответа GPT: разделитель «-» на отдельной строке или inline."""
    raw = (text or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?:\n\s*-\s*\n|\n\s*-\s+|\r\n\s*-\s+)", raw)
    if len(parts) <= 1 and " - " in raw:
        parts = raw.split(" - ")
    out: list[str] = []
    for p in parts:
        s = " ".join(p.split())
        if len(s) >= _MIN_BLOCK_LEN:
            out.append(s)
    return out


def split_voiceover_locally(text: str, *, target_len: int = 70) -> list[str]:
    """Грубая разбивка voiceover.txt по предложениям (~45–100 симв)."""
    raw = " ".join((text or "").split())
    if len(raw) < _MIN_BLOCK_LEN:
        return []
    sentences = re.split(r"(?<=[.!?…])\s+", raw)
    blocks: list[str] = []
    buf = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if not buf:
            buf = sent
        elif len(buf) + 1 + len(sent) <= target_len + 30:
            buf = f"{buf} {sent}"
        else:
            if len(buf) >= _MIN_BLOCK_LEN:
                blocks.append(buf)
            buf = sent
    if buf and len(buf) >= _MIN_BLOCK_LEN:
        blocks.append(buf)
    return blocks


def write_voiceover_blocks_to_xlsx(xlsx_path: Path, blocks: list[str]) -> int:
    """Пишет блоки в строку 49 листа «план», колонки C..N."""
    if not blocks:
        return 0
    path = Path(xlsx_path)
    wb = load_workbook(filename=str(path))
    ws = _resolve_plan_sheet(wb)
    if ws is None:
        if SHEET_PLAN_V8 in wb.sheetnames:
            ws = wb[SHEET_PLAN_V8]
        else:
            ws = wb.active
            ws.title = SHEET_PLAN_V8
    for i, block in enumerate(blocks):
        ws.cell(row=ROW_VOICEOVER_V8, column=3 + i, value=block)
    wb.save(path)
    logger.info(
        "voiceover_split_local: wrote {} blocks to {} R{}",
        len(blocks),
        path.name,
        ROW_VOICEOVER_V8,
    )
    return len(blocks)
