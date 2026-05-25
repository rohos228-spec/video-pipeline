"""Шаг 2: общий план → закадровый текст (xlsx-flow, как Telegram _run_script_xlsx)."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLKind, Project, ProjectStatus
from app.services import xlsx_step_runners as xsr
from app.services.hitl import send_hitl_text
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.scripting:
        return
    logger.info("[#{}] make_script (xlsx-flow) starting", project.id)

    _result, voiceover_text = await xsr.run_script_xlsx(project)

    if len(voiceover_text) < 200:
        raise RuntimeError("ChatGPT вернул пустой/слишком короткий сценарий")

    project.script_text = voiceover_text
    project.status = ProjectStatus.script_ready
    await session.flush()

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
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
