"""Запись полей кадра на лист «план» (v8-xlsx).

Колонки кадров: 3..N (кадр 1 → col 3). Строки см. `xlsx_v8_import`.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from openpyxl import load_workbook

from app.models import Project
from app.services.xlsx_v8_import import ROW_VIDEO_PROMPT_V8, SHEET_PLAN_V8
from app.storage.project_sheet import _file_lock


def plan_frame_column(frame_number: int) -> int:
    """Кадр N (1-based) → колонка на листе «план»."""
    return frame_number + 2


def write_plan_animation_prompt(
    project: Project,
    frame_number: int,
    animation_prompt: str,
) -> bool:
    """Пишет промт анимации в строку 48 листа «план» для кадра `frame_number`."""
    path = project.data_dir / "project.xlsx"
    if not path.exists():
        return False
    col = plan_frame_column(frame_number)
    text = (animation_prompt or "").strip()
    if not text:
        return False
    try:
        with _file_lock(path):
            wb = load_workbook(path)
            if SHEET_PLAN_V8 not in wb.sheetnames:
                wb.close()
                return False
            ws = wb[SHEET_PLAN_V8]
            ws.cell(row=ROW_VIDEO_PROMPT_V8, column=col, value=text)
            wb.save(path)
            wb.close()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] write_plan_animation_prompt frame {} failed: {}",
            project.id,
            frame_number,
            e,
        )
        return False
