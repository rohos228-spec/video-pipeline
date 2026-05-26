"""Запись полей кадра на лист «план» (v8-xlsx).

Колонки кадров: 3..N (кадр 1 → col 3). Строки см. `xlsx_v8_import`.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from openpyxl import load_workbook

from app.models import Project
from app.services.xlsx_v8_import import (
    ROW_VIDEO_PROMPT_V8,
    ROW_VOICEOVER_V8,
    SHEET_PLAN_V8,
    _cell_text,
)
from app.storage.project_sheet import _file_lock


def _resolve_plan_sheet(wb):
    """Лист «план» (v8), без учёта регистра имени."""
    if SHEET_PLAN_V8 in wb.sheetnames:
        return wb[SHEET_PLAN_V8]
    low = SHEET_PLAN_V8.casefold()
    for name in wb.sheetnames:
        if name.casefold() == low:
            return wb[name]
    return None


def plan_frame_column(frame_number: int) -> int:
    """Кадр N (1-based) → колонка на листе «план»."""
    return frame_number + 2


def read_plan_voiceover(project: Project, frame_number: int) -> str | None:
    """Закадровый текст кадра — строка 49 листа «план» (v8)."""
    path = project.data_dir / "project.xlsx"
    if not path.exists():
        return None
    col = plan_frame_column(frame_number)
    try:
        with _file_lock(path):
            wb = load_workbook(path, read_only=True, data_only=True)
            ws = _resolve_plan_sheet(wb)
            if ws is None:
                wb.close()
                return None
            text = _cell_text(ws, ROW_VOICEOVER_V8, col)
            wb.close()
            return text
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] read_plan_voiceover frame {} failed: {}",
            project.id,
            frame_number,
            e,
        )
        return None


def write_plan_animation_prompt(
    project: Project,
    frame_number: int,
    animation_prompt: str,
) -> bool:
    """Пишет промт анимации в строку 48 листа «план» для кадра `frame_number`."""
    path = project.data_dir / "project.xlsx"
    if not path.exists():
        logger.warning(
            "[#{}] write_plan_animation_prompt: нет {}",
            project.id,
            path,
        )
        return False
    col = plan_frame_column(frame_number)
    text = (animation_prompt or "").strip()
    if not text:
        return False
    try:
        with _file_lock(path):
            wb = load_workbook(path)
            ws = _resolve_plan_sheet(wb)
            if ws is None:
                wb.close()
                logger.warning(
                    "[#{}] write_plan_animation_prompt: лист «{}» не найден в {}",
                    project.id,
                    SHEET_PLAN_V8,
                    path.name,
                )
                return False
            ws.cell(row=ROW_VIDEO_PROMPT_V8, column=col, value=text)
            wb.save(path)
            wb.close()
        logger.info(
            "[#{}] plan R{} col {} ← animation_prompt ({} симв.)",
            project.id,
            ROW_VIDEO_PROMPT_V8,
            col,
            len(text),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] write_plan_animation_prompt frame {} failed: {}",
            project.id,
            frame_number,
            e,
        )
        return False
