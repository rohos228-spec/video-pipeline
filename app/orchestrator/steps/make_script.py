"""Шаг 3: общий план → сценарий озвучки с разбиением на ячейки."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.generation_options import render_settings_for_gpt
from app.models import HITLKind, Project, ProjectStatus, PromptKey
from app.services.hitl import send_hitl_text
from app.services.prompts import get_active_prompt
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.scripting:
        return
    if not project.general_plan:
        raise RuntimeError("general_plan пуст — нечего превращать в сценарий")
    logger.info("[#{}] make_script starting", project.id)

    master = await get_active_prompt(session, PromptKey.SCRIPT_SHORTS)
    tech_block = render_settings_for_gpt(
        project.image_generator,
        project.aspect_ratio,
        project.image_resolution,
        project.video_generator,
        project.video_resolution,
    )
    full_prompt = (
        tech_block + "\n" + master + "\n\n---\n\n"
        + "Лист «Общий план»:\n"
        + project.general_plan
    )

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        reply = await gpt.ask_fresh(full_prompt, timeout=420)

    if not reply or len(reply) < 200:
        raise RuntimeError("ChatGPT вернул пустой сценарий")

    project.script_text = reply
    project.status = ProjectStatus.script_ready
    await session.flush()

    # На «Общий план ролика» текст сценария НЕ пишем — пользователь просит,
    # чтобы он лежал на «Кадры» в строке «закадровый текст», по столбцу на
    # кадр. Это сделает шаг 3 (split_frames) после разбивки.
    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet status write failed: {}", project.id, e)

    await send_hitl_text(
        bot, session, project,
        kind=HITLKind.approve_script,
        title=f"Сценарий #{project.id}",
        text=reply,
        payload={"step": "script"},
    )
