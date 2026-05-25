"""Шаг 2: общий план → закадровый текст (xlsx-flow через ChatGPT web).

Мастер-промт уходит файлом; в чат — только override или дефолт.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import HITLKind, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services.hitl import send_hitl_text
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.scripting:
        return
    logger.info("[#{}] make_script (xlsx-flow) starting", project.id)

    sheet = _sheet_for_project(project)
    xlsx_path: Path = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )
    if not xlsx_path.exists():
        raise RuntimeError(f"make_script: project.xlsx не найден: {xlsx_path}")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_script_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "script", prompt_file_name=prompt_file.name
    )
    voiceover_path = xlsx_path.parent / "voiceover.txt"
    downloaded = tmp_dir / f"voiceover_{ts}.txt"

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        await cx.ask_with_prompt_files(
            gpt,
            chat_msg,
            [prompt_file, xlsx_path],
            timeout=900,
            project_id=project.id,
        )
        voiceover_text = await cx.download_text_attachment(
            gpt, downloaded, timeout=900
        )

    cx.save_voiceover_text(project, voiceover_path, voiceover_text)

    if len(voiceover_text) < 200:
        raise RuntimeError("ChatGPT вернул пустой/слишком короткий сценарий")

    project.script_text = voiceover_text
    project.status = ProjectStatus.script_ready
    await session.flush()

    try:
        sheet.write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet status write failed: {}", project.id, e)

    await send_hitl_text(
        bot,
        session,
        project,
        kind=HITLKind.approve_script,
        title=f"Закадровый текст #{project.id}",
        text=voiceover_text,
        payload={"step": "script"},
    )
