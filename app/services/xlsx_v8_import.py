"""Импорт v8-xlsx → БД. Используется в двух местах:

1. _backfill_from_disk на старте бота — подтягиваем xlsx/voiceover.txt
   в БД для всех проектов, чтобы recompute_status не откатил статус
   из-за пустых полей.
2. После xlsx-flow шагов 1 («План») и 3 («Разбивка») в TG-боте —
   синхронизируем свежий xlsx, который GPT прислал, в БД.

Логика вытащена из standalone-скрипта `import_from_xlsx.py` (см. там
оригинал и описание формата v8).

v8-шаблон отличается от старого (v7):
  - лист «Общий план» (без «ролика» в имени)
  - лист «план» (вместо «Кадры»), кадры стоят колонками 3..N,
    voiceover лежит в строке 49.

Идемпотентный: повторный запуск ничего не ломает, обновляет только то,
что изменилось.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project

# --- константы под v8-шаблон ---------------------------------------------
SHEET_GENERAL_V8 = "Общий план"
SHEET_PLAN_V8 = "план"
# В v8-шаблоне «план» каждый кадр — это колонка (col=3..N). Эти строки —
# поля одного кадра. См. templates/project_template_v8.xlsx (column A):
ROW_IMAGE_PROMPT_V8 = 45  # «промт для картинки 1»
# R46/R47 — резервные «картинка 2/3» (модель пока одну хранит, см. Frame.image_prompt)
ROW_VIDEO_PROMPT_V8 = 48  # «промт для видео»
ROW_VOICEOVER_V8 = 49     # «закадровый текст»
ROW_DURATION_V8 = 50      # «Время на кадр»

# Длительность кадра — проп. длине voiceover-блока (русская речь ~14 симв/сек).
CHARS_PER_SEC = 14.0
MIN_FRAME = 1.5
MAX_FRAME = 6.0


def _resolve_plan_sheet(wb):
    """Лист «план» (v8), без учёта регистра имени."""
    if SHEET_PLAN_V8 in wb.sheetnames:
        return wb[SHEET_PLAN_V8]
    low = SHEET_PLAN_V8.casefold()
    for name in wb.sheetnames:
        if name.casefold() == low:
            return wb[name]
    return None


def has_v8_plan_sheet(wb) -> bool:
    return _resolve_plan_sheet(wb) is not None


def _distribute_durations(cells: list[str]) -> list[float]:
    if not cells:
        return []
    return [
        round(min(max(len(c) / CHARS_PER_SEC, MIN_FRAME), MAX_FRAME), 2)
        for c in cells
    ]


def _read_general_plan(wb) -> str | None:
    if SHEET_GENERAL_V8 not in wb.sheetnames:
        return None
    ws = wb[SHEET_GENERAL_V8]
    lines: list[str] = []
    block_header_row: int | None = None

    for r in range(1, min(ws.max_row, 200) + 1):
        a = ws.cell(row=r, column=1).value
        b = ws.cell(row=r, column=2).value
        a_s = str(a).strip() if a is not None else ""
        b_s = str(b).strip() if b is not None else ""

        if a_s and not b_s:
            lines.append(f"\n## {a_s}\n")
            block_header_row = r + 1
            continue

        if block_header_row == r:
            headers = [
                ws.cell(row=r, column=c).value for c in range(1, 6)
            ]
            if all(h for h in headers):
                block_header_row = -1
                continue
            block_header_row = None

        if a_s and b_s:
            lines.append(f"**{a_s}:** {b_s}")
            continue

        if block_header_row == -1 and any(
            ws.cell(row=r, column=c).value for c in range(1, 6)
        ):
            row_cells = [
                str(ws.cell(row=r, column=c).value or "").strip()
                for c in range(1, 6)
            ]
            if row_cells[0]:
                lines.append(f"\n### {row_cells[0]}")
            for label, idx in [
                ("Основная мысль", 1),
                ("Подтемы", 2),
                ("Функция", 3),
                ("Как подводит к следующему", 4),
            ]:
                if row_cells[idx]:
                    lines.append(f"- **{label}:** {row_cells[idx]}")

    text = "\n".join(line for line in lines if line.strip()).strip()
    return text if text else None


def _cell_text(ws, row: int, col: int) -> str | None:
    """Прочитать ячейку как trim-string, схлопнув whitespace. None если пусто."""
    v = ws.cell(row=row, column=col).value
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return " ".join(s.split())


def _cell_float(ws, row: int, col: int) -> float | None:
    v = ws.cell(row=row, column=col).value
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _read_voiceover_blocks(wb) -> list[str]:
    ws = _resolve_plan_sheet(wb)
    if ws is None:
        return []
    out: list[str] = []
    for col in range(3, ws.max_column + 1):
        s = _cell_text(ws, ROW_VOICEOVER_V8, col)
        if s is None:
            continue
        out.append(s)
    return out


def _read_frame_fields(wb) -> list[dict[str, Any]]:
    """Читает поля кадров с листа «план» v8: image_prompt (R45),
    animation_prompt (R48), voiceover (R49), duration (R50). Возвращает
    список по фреймам (порядок = порядок колонок 3..N, в которых есть
    voiceover). Каждый элемент — dict с ключами
    {image_prompt, animation_prompt, voiceover_text, duration_seconds}.

    Кадр считается «существующим», если в колонке непустой voiceover —
    остальное опционально. Это согласовано с _read_voiceover_blocks.
    """
    ws = _resolve_plan_sheet(wb)
    if ws is None:
        return []
    out: list[dict[str, Any]] = []
    for col in range(3, ws.max_column + 1):
        voice = _cell_text(ws, ROW_VOICEOVER_V8, col)
        if voice is None:
            continue
        out.append({
            "voiceover_text": voice,
            "image_prompt": _cell_text(ws, ROW_IMAGE_PROMPT_V8, col),
            "animation_prompt": _cell_text(ws, ROW_VIDEO_PROMPT_V8, col),
            "duration_seconds": _cell_float(ws, ROW_DURATION_V8, col),
        })
    return out


async def import_v8_xlsx(
    session: AsyncSession,
    project: Project,
    xlsx_path: Path,
    *,
    keep_fields: bool = True,
    update_frames_voiceover: bool = False,
) -> dict[str, Any]:
    """Подтягиваем v8-xlsx в БД для проекта.

    `keep_fields=True` (дефолт) — НЕ перезаписываем непустые
    general_plan/script_text, только заполняем пустые. Это безопасный
    режим для бэкфилла на старте.

    `keep_fields=False` — перезаписываем (используется после xlsx-flow
    шагов плана/разбивки, когда юзер только что прислал свежий xlsx).

    `update_frames_voiceover` — если True, обновляем voiceover_text у
    существующих Frame'ов (для xlsx-flow шага 3). Иначе только создаём
    недостающие.
    """
    from openpyxl import load_workbook

    summary: dict[str, Any] = {
        "project_fields_changed": [],
        "frames_created": [],
        "frames_updated": [],
    }

    if not xlsx_path.exists():
        return {"error": f"файл не найден: {xlsx_path}"}

    try:
        wb = load_workbook(filename=str(xlsx_path), data_only=True)
    except Exception as e:  # noqa: BLE001
        return {"error": f"openpyxl: {e}"}

    # --- general_plan ---
    new_plan = _read_general_plan(wb)
    if new_plan:
        if keep_fields:
            if not project.general_plan:
                project.general_plan = new_plan
                summary["project_fields_changed"].append("general_plan")
                logger.info(
                    "[#{}] xlsx-v8→DB: general_plan заполнен ({} симв)",
                    project.id, len(new_plan),
                )
        else:
            if project.general_plan != new_plan:
                project.general_plan = new_plan
                summary["project_fields_changed"].append("general_plan")
                logger.info(
                    "[#{}] xlsx-v8→DB: general_plan обновлён ({} симв)",
                    project.id, len(new_plan),
                )

    # --- script_text + frames из voiceover-блоков ---
    blocks = _read_voiceover_blocks(wb)
    if blocks:
        new_script = " ".join(blocks)
        if keep_fields:
            if not project.script_text:
                project.script_text = new_script
                summary["project_fields_changed"].append("script_text")
                logger.info(
                    "[#{}] xlsx-v8→DB: script_text заполнен из блоков "
                    "({} симв, {} блоков)",
                    project.id, len(new_script), len(blocks),
                )
        else:
            if project.script_text != new_script:
                project.script_text = new_script
                summary["project_fields_changed"].append("script_text")
                logger.info(
                    "[#{}] xlsx-v8→DB: script_text обновлён ({} симв, "
                    "{} блоков)",
                    project.id, len(new_script), len(blocks),
                )

        # Frame'ы — создаём недостающие, прицепляем поля из v8 (image_prompt,
        # animation_prompt, duration). Это единственный путь для v8-проектов
        # подтянуть промты, заполненные ChatGPT-ом через enrich-слоты, в БД —
        # старый xlsx_sync (лист «Кадры», R29) на v8-файле молча no-op.
        existing = (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )
        ).scalars().all()
        by_number = {f.number: f for f in existing}

        # Поля по кадрам — image_prompt, animation_prompt, voiceover, duration.
        # Длины списков совпадают: оба фильтруют по непустому voiceover в
        # колонке (см. _read_frame_fields / _read_voiceover_blocks).
        frame_fields = _read_frame_fields(wb)
        fallback_durations = _distribute_durations(blocks)
        t = 0.0
        prompts_synced: list[int] = []
        for i, (cell, fields) in enumerate(
            zip(blocks, frame_fields, strict=True), start=1
        ):
            dur = fields.get("duration_seconds") or fallback_durations[i - 1]
            start_ts = t
            end_ts = t + dur
            fr = by_number.get(i)
            if fr is None:
                session.add(
                    Frame(
                        project_id=project.id,
                        number=i,
                        voiceover_text=cell,
                        image_prompt=fields.get("image_prompt"),
                        animation_prompt=fields.get("animation_prompt"),
                        start_ts=start_ts,
                        end_ts=end_ts,
                        duration_seconds=dur,
                    )
                )
                summary["frames_created"].append(i)
                if fields.get("image_prompt") or fields.get("animation_prompt"):
                    prompts_synced.append(i)
            else:
                changed = False
                if update_frames_voiceover and fr.voiceover_text != cell:
                    fr.voiceover_text = cell
                    changed = True
                # ROOT FIX: подтягиваем image_prompt / animation_prompt из v8.
                # Перезаписываем только когда в xlsx есть непустое значение и
                # оно отличается от текущего — чтобы случайно очищенная ячейка
                # не стёрла GPT-промт в БД.
                new_imgp = fields.get("image_prompt")
                if new_imgp and new_imgp != fr.image_prompt:
                    fr.image_prompt = new_imgp
                    changed = True
                    prompts_synced.append(i)
                new_animp = fields.get("animation_prompt")
                if new_animp and new_animp != fr.animation_prompt:
                    fr.animation_prompt = new_animp
                    changed = True
                new_dur = fields.get("duration_seconds")
                if new_dur is not None and abs((fr.duration_seconds or 0.0) - new_dur) > 0.01:
                    fr.duration_seconds = new_dur
                    changed = True
                if changed and i not in summary["frames_updated"]:
                    summary["frames_updated"].append(i)
            t = end_ts

        if prompts_synced:
            summary["prompts_synced"] = prompts_synced
            logger.info(
                "[#{}] xlsx-v8→DB: подтянуты image/anim prompts для кадров {}",
                project.id, prompts_synced,
            )
        if summary["frames_created"]:
            logger.info(
                "[#{}] xlsx-v8→DB: создано {} Frame'ов",
                project.id, len(summary["frames_created"]),
            )
        if summary["frames_updated"]:
            logger.info(
                "[#{}] xlsx-v8→DB: обновлено {} Frame'ов (v8-поля)",
                project.id, len(summary["frames_updated"]),
            )

    await session.flush()
    return summary
