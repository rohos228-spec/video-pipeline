"""Запись полей кадра на лист «план» (v8-xlsx).

Колонки кадров: 3..N (кадр 1 → col 3). Строки см. `xlsx_v8_import`.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from openpyxl import load_workbook

from app.models import Project
from app.services.xlsx_v8_import import (
    ROW_IMAGE_PROMPT_V8,
    ROW_VIDEO_PROMPT_V8,
    ROW_VOICEOVER_V8,
    _cell_text,
    _resolve_plan_sheet,
)

SHEET_PLAN_V8 = "план"
from app.storage.project_sheet import _file_lock


def plan_frame_column(frame_number: int) -> int:
    """Кадр N (1-based) → колонка на листе «план»."""
    return frame_number + 2


def read_plan_voiceover(project: Project, frame_number: int) -> str | None:
    """Закадровый текст кадра — строка 49 листа «план» (v8)."""
    cells = read_plan_voiceover_cells(project, [frame_number])
    text = cells[0][1] if cells else ""
    return text or None


def read_plan_animation_prompt_cells(
    project: Project,
    frame_numbers: list[int],
) -> list[tuple[int, str]]:
    """Промт анимации кадра — строка 48 листа «план» (v8)."""
    if not frame_numbers:
        return []
    path = project.data_dir / "project.xlsx"
    if not path.exists():
        return [(n, "") for n in frame_numbers]

    out: dict[int, str] = dict.fromkeys(frame_numbers, "")
    try:
        with _file_lock(path):
            wb = load_workbook(path, read_only=True, data_only=True)
            ws = _resolve_plan_sheet(wb)
            if ws is not None:
                for frame_number in frame_numbers:
                    col = plan_frame_column(frame_number)
                    text = _cell_text(ws, ROW_VIDEO_PROMPT_V8, col)
                    out[frame_number] = (text or "").strip()
            wb.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] read_plan_animation_prompt_cells failed: {}",
            project.id,
            e,
        )
    return [(n, out[n]) for n in frame_numbers]


def read_plan_voiceover_cells(
    project: Project,
    frame_numbers: list[int],
) -> list[tuple[int, str]]:
    """Текст каждого кадра: строка 49 листа «план», одна колонка = одно видео."""
    if not frame_numbers:
        return []
    path = project.data_dir / "project.xlsx"
    if not path.exists():
        return [(n, "") for n in frame_numbers]

    out: dict[int, str] = dict.fromkeys(frame_numbers, "")
    try:
        with _file_lock(path):
            wb = load_workbook(path, read_only=True, data_only=True)
            ws = _resolve_plan_sheet(wb)
            if ws is not None:
                for frame_number in frame_numbers:
                    col = plan_frame_column(frame_number)
                    text = _cell_text(ws, ROW_VOICEOVER_V8, col)
                    out[frame_number] = (text or "").strip()
            wb.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] read_plan_voiceover_cells failed: {}",
            project.id,
            e,
        )
    return [(n, out[n]) for n in frame_numbers]


def write_plan_image_prompt(
    project: Project,
    frame_number: int,
    image_prompt: str,
) -> bool:
    """Промт картинки — строка 45 листа «план» (v8)."""
    path = project.data_dir / "project.xlsx"
    if not path.exists():
        return False
    col = plan_frame_column(frame_number)
    text = (image_prompt or "").strip()
    if not text:
        return False
    try:
        with _file_lock(path):
            wb = load_workbook(path)
            ws = _resolve_plan_sheet(wb)
            if ws is None:
                wb.close()
                return False
            ws.cell(row=ROW_IMAGE_PROMPT_V8, column=col, value=text)
            wb.save(path)
            wb.close()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] write_plan_image_prompt frame {} failed: {}",
            project.id,
            frame_number,
            e,
        )
        return False


def write_plan_image_prompts_bulk(
    project: Project,
    prompts_by_frame: dict[int, str],
) -> int:
    """Массовая запись image_prompt в R45. Возвращает число записанных кадров."""
    if not prompts_by_frame:
        return 0
    path = project.data_dir / "project.xlsx"
    if not path.exists():
        return 0
    written = 0
    try:
        with _file_lock(path):
            wb = load_workbook(path)
            ws = _resolve_plan_sheet(wb)
            if ws is None:
                wb.close()
                return 0
            for frame_number, text in prompts_by_frame.items():
                t = (text or "").strip()
                if not t:
                    continue
                col = plan_frame_column(frame_number)
                ws.cell(row=ROW_IMAGE_PROMPT_V8, column=col, value=t)
                written += 1
            if written:
                wb.save(path)
            wb.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] write_plan_image_prompts_bulk failed: {}",
            project.id,
            e,
        )
        return 0
    if written:
        logger.info(
            "[#{}] plan R{}: записано image_prompt для {} кадров",
            project.id,
            ROW_IMAGE_PROMPT_V8,
            written,
        )
    return written


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
