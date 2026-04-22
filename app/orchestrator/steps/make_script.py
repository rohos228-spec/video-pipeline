"""Шаг 3: общий план → сценарий озвучки с разбиением на ячейки."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import HITLKind, Project, ProjectStatus, PromptKey
from app.services.hitl import send_hitl_text
from app.services.prompts import get_active_prompt


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.plan_ready:
        return
    if not project.general_plan:
        raise RuntimeError("general_plan пуст — нечего превращать в сценарий")
    logger.info("[#{}] make_script starting", project.id)

    master = await get_active_prompt(session, PromptKey.SCRIPT_SHORTS)
    full_prompt = (
        master + "\n\n---\n\n"
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

    await send_hitl_text(
        bot, session, project,
        kind=HITLKind.approve_script,
        title=f"Сценарий #{project.id}",
        text=reply,
        payload={"step": "script"},
    )
