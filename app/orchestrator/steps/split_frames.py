"""Шаг 3: разбивка (xlsx-flow, как Telegram _run_split_xlsx)."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus
from app.services import xlsx_step_runners as xsr
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    if project.status is not ProjectStatus.splitting:
        return
    logger.info("[#{}] split_frames (xlsx-flow) starting", project.id)

    existing_frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    if len(existing_frames) >= 2:
        logger.info(
            "[#{}] split_frames: в БД уже {} кадров — пропуск GPT",
            project.id,
            len(existing_frames),
        )
        project.status = ProjectStatus.frames_ready
        await session.flush()
        return

    result = await xsr.run_split_xlsx(project)
    sync_info = await xsr.sync_after_split(session, project, result.project_xlsx)
    if sync_info:
        logger.info("[#{}] split_frames sync: {}", project.id, sync_info)

    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        blocks = xsr._count_v8_voiceover_blocks(result.project_xlsx)
        diag = xsr.diagnose_split_xlsx(result.project_xlsx)
        raise RuntimeError(
            "после xlsx-sync кадры не созданы — в project.xlsx найдено "
            f"{blocks} voiceover-блоков. {diag} "
            "Проверь: строка 49 листа «план», колонки C..N."
        )

    project.status = ProjectStatus.frames_ready
    await session.flush()
    logger.info("[#{}] split_frames: {} кадров из xlsx", project.id, len(frames))

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet split write failed: {}", project.id, e)
