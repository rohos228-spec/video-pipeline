"""Шаг 3: разбивка (xlsx-flow, как Telegram _run_split_xlsx)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services import xlsx_gpt_flow as xgf
from app.services.xlsx_versioning import backup_to_old, replace_with
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    if project.status is not ProjectStatus.splitting:
        return
    logger.info("[#{}] split_frames (xlsx-flow) starting", project.id)

    existing = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalars().all()
    if existing:
        logger.info("[#{}] frames уже есть ({}), пропуск", project.id, len(existing))
        project.status = ProjectStatus.frames_ready
        return

    proj_xlsx = project.data_dir / "project.xlsx"
    if not proj_xlsx.exists():
        sheet = _sheet_for_project(project)
        proj_xlsx = sheet.ensure_initialized(
            project_id=project.id, slug=project.slug
        )
    if not proj_xlsx.exists():
        raise RuntimeError(f"split_frames: project.xlsx не найден: {proj_xlsx}")

    voiceover = proj_xlsx.parent / "voiceover.txt"
    if not voiceover.exists() and project.script_text:
        voiceover.write_text(project.script_text.strip(), encoding="utf-8")
    if not voiceover.exists():
        raise RuntimeError(
            "voiceover.txt не найден — сначала пройди шаг «Закадровый текст»"
        )

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_split_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "split", prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"split_{ts}.xlsx"

    async def _do() -> None:
        await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx, voiceover],
            downloaded,
            project_id=project.id,
            validate_xlsx_download=True,
        )
        backup_to_old(proj_xlsx)
        replace_with(proj_xlsx, downloaded)

    await xgf.run_under_xlsx_lock(project.id, "split", _do)

    await cx.sync_project_xlsx(
        session,
        project,
        proj_xlsx,
        keep_fields=False,
        update_frames_voiceover=True,
    )

    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError(
            "после xlsx-sync кадры не созданы — проверь ответ ChatGPT"
        )

    project.status = ProjectStatus.frames_ready
    await session.flush()
    logger.info("[#{}] split_frames: {} кадров из xlsx", project.id, len(frames))

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet split write failed: {}", project.id, e)
