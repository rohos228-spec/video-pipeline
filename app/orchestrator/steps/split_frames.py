"""Шаг 3: разбивка закадрового текста на блоки (xlsx-flow через ChatGPT).

Мастер-промт уходит файлом вместе с project.xlsx и voiceover.txt;
в чат — только override или дефолтное сопр. сообщение.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import Frame, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
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

    sheet = _sheet_for_project(project)
    xlsx_path: Path = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )
    if not xlsx_path.exists():
        raise RuntimeError(f"split_frames: project.xlsx не найден: {xlsx_path}")

    voiceover = xlsx_path.parent / "voiceover.txt"
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

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        await cx.ask_with_prompt_files(
            gpt,
            chat_msg,
            [prompt_file, xlsx_path, voiceover],
            timeout=900,
            project_id=project.id,
        )
        await cx.download_and_replace_xlsx(
            gpt, xlsx_path, downloaded, timeout=900
        )

    await cx.sync_project_xlsx(
        session,
        project,
        xlsx_path,
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
        sheet.write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet split write failed: {}", project.id, e)
