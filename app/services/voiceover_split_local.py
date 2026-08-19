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


def frames_spec_cell_stats(frames_spec: list[dict]) -> dict[str, float | int]:
    lens: list[int] = []
    for item in frames_spec:
        if not isinstance(item, dict):
            continue
        text = str(
            item.get("закадр")
            or item.get("voiceover_text")
            or item.get("voiceover")
            or ""
        ).strip()
        if text:
            lens.append(len(text))
    if not lens:
        return {"n": 0, "min": 0, "max": 0, "avg": 0.0}
    return {
        "n": len(lens),
        "min": min(lens),
        "max": max(lens),
        "avg": round(sum(lens) / len(lens), 1),
    }


def _split_oversized(text: str, *, max_chars: int, target: int) -> list[str]:
    raw = " ".join((text or "").split())
    if not raw:
        return []
    if len(raw) <= max_chars:
        return [raw]
    sentences = re.split(r"(?<=[.!?…])\s+", raw)
    if len(sentences) <= 1:
        words = raw.split()
        out: list[str] = []
        buf: list[str] = []
        for w in words:
            # одно «слово» длиннее max — режем посимвольно
            if len(w) > max_chars:
                if buf:
                    out.append(" ".join(buf).strip())
                    buf = []
                step = max(1, min(max_chars, target))
                for i in range(0, len(w), step):
                    chunk = w[i : i + step]
                    if chunk:
                        out.append(chunk)
                continue
            cand = (" ".join(buf + [w])).strip()
            if buf and len(cand) > max_chars:
                chunk = " ".join(buf).strip()
                if chunk:
                    out.append(chunk)
                buf = [w]
            else:
                buf.append(w)
        if buf:
            out.append(" ".join(buf).strip())
        return [b for b in out if b]
    out: list[str] = []
    buf = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if not buf:
            buf = sent
            continue
        if len(buf) + 1 + len(sent) <= max_chars:
            buf = f"{buf} {sent}"
        else:
            out.append(buf)
            buf = sent
    if buf:
        out.append(buf)
    fixed: list[str] = []
    for b in out:
        if len(b) <= max_chars:
            fixed.append(b)
        else:
            fixed.extend(_split_oversized(b, max_chars=max_chars, target=target))
    return fixed


def enforce_split_cell_limits(
    blocks: list[str],
    *,
    min_chars: int | None,
    max_chars: int | None,
    avg_min: int | None = None,
    avg_max: int | None = None,
) -> list[str]:
    """Склеить короткие / разрезать длинные ячейки под лимиты из настроек."""
    cleaned = [" ".join(str(b).split()) for b in blocks if str(b).strip()]
    if not cleaned:
        return []
    if min_chars is None and max_chars is None:
        return cleaned

    lo = int(min_chars) if min_chars is not None else 1
    hi = int(max_chars) if max_chars is not None else 10_000
    if hi < lo:
        hi = lo
    if avg_min is not None and avg_max is not None and avg_max >= avg_min:
        target = int(round((avg_min + avg_max) / 2))
    elif avg_min is not None:
        target = int(avg_min)
    elif avg_max is not None:
        target = int(avg_max)
    else:
        target = int(round((lo + hi) / 2))
    target = max(lo, min(hi, target))

    pieces: list[str] = []
    for b in cleaned:
        pieces.extend(_split_oversized(b, max_chars=hi, target=target))

    merged: list[str] = []
    i = 0
    while i < len(pieces):
        cur = pieces[i]
        while i + 1 < len(pieces) and len(cur) < lo:
            nxt = pieces[i + 1]
            if len(cur) + 1 + len(nxt) <= hi:
                cur = f"{cur} {nxt}"
                i += 1
            else:
                break
        merged.append(cur)
        i += 1

    if len(merged) >= 2 and len(merged[-1]) < lo:
        if len(merged[-2]) + 1 + len(merged[-1]) <= hi:
            merged[-2] = f"{merged[-2]} {merged[-1]}"
            merged.pop()

    return [m for m in merged if len(m) >= _MIN_BLOCK_LEN or len(merged) == 1]


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
