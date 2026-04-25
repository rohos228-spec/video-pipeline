"""Шаг 4: разобрать сценарий на ячейки и создать записи Frame в БД.

Формат выдачи SCRIPT_SHORTS:
  <ячейка 1>
  <ячейка 2>
  ...
  ИТОГО: N ячеек, M знаков, ~T секунд.

Пустые строки игнорируем, строку с "ИТОГО" отбрасываем.
Длительность распределяем пропорционально длине каждой ячейки
внутри диапазона 2–4 сек так, чтобы сумма была 60–75 сек.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus

MIN_FRAME = 2.0
MAX_FRAME = 4.0
TARGET_TOTAL = 65.0  # середина целевого окна 60–75 сек


def _parse_cells(script: str) -> list[str]:
    cells: list[str] = []
    for raw in script.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("итого"):
            continue
        # отбрасываем нумерацию "1. ..." / "1) ..." если вдруг
        if line[:2].isdigit() or (line[0].isdigit() and (line[1] in ".)") and line[2] == " "):
            line = line.split(" ", 1)[-1].strip()
        cells.append(line)
    return cells


def _distribute_durations(cells: list[str]) -> list[float]:
    if not cells:
        return []
    lengths = [max(len(c), 1) for c in cells]
    total_len = sum(lengths)
    raw = [TARGET_TOTAL * (length / total_len) for length in lengths]
    clamped = [min(max(x, MIN_FRAME), MAX_FRAME) for x in raw]
    # если после клампинга сумма вышла за 60–75 — пропорционально подгоняем
    s = sum(clamped)
    target = min(max(s, 60.0), 75.0)
    if s > 0:
        factor = target / s
        clamped = [min(max(x * factor, MIN_FRAME), MAX_FRAME) for x in clamped]
    return [round(x, 2) for x in clamped]


async def run(session: AsyncSession, project: Project) -> None:
    if project.status is not ProjectStatus.script_ready:
        return
    if not project.script_text:
        raise RuntimeError("script_text пуст")
    logger.info("[#{}] split_frames starting", project.id)

    # Идемпотентность: если фреймы уже есть — не трогаем.
    # Явный await-запрос вместо project.frames (lazy-load в async SQLAlchemy
    # падает с MissingGreenlet).
    existing = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    if existing:
        logger.info("[#{}] frames уже есть ({})", project.id, len(existing))
        project.status = ProjectStatus.frames_ready
        return

    cells = _parse_cells(project.script_text)
    if not cells:
        raise RuntimeError("не удалось выделить ни одной ячейки из сценария")

    durations = _distribute_durations(cells)
    t = 0.0
    for i, (cell, dur) in enumerate(zip(cells, durations, strict=True), start=1):
        start_ts = t
        end_ts = t + dur
        session.add(
            Frame(
                project_id=project.id,
                number=i,
                voiceover_text=cell,
                start_ts=start_ts,
                end_ts=end_ts,
                duration_seconds=dur,
            )
        )
        t = end_ts

    project.status = ProjectStatus.frames_ready
    await session.flush()
    logger.info("[#{}] split_frames: {} ячеек, итого {:.2f} сек", project.id, len(cells), t)
