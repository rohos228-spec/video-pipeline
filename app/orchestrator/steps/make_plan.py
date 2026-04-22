"""Шаг 1–2: тема → общий план ролика (ChatGPT web + мастер-промт PLAN_SHORTS).
Затем HITL-одобрение в Telegram."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import HITLKind, Project, ProjectStatus
from app.services.hitl import send_hitl_text, wait_for_decision
from app.services.prompts import get_active_prompt
from app.models import PromptKey


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.planning:
        return
    logger.info("[#{}] make_plan starting: '{}'", project.id, project.topic)

    master = await get_active_prompt(session, PromptKey.PLAN_SHORTS)
    hero_hint = {
        "hero": "Игнорируй автоматическое определение hero_needed, выставь hero_needed=true.",
        "no_hero": "Игнорируй автоматическое определение hero_needed, выставь hero_needed=false.",
        "auto": "",
    }.get(project.hero_mode, "")

    full_prompt = (
        master
        + "\n\n---\n\n"
        + "Тема ролика (исходный материал для анализа):\n"
        + project.topic
        + ("\n\nДополнительное указание: " + hero_hint if hero_hint else "")
    )

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        reply = await gpt.ask_fresh(full_prompt, timeout=420)

    if not reply or len(reply) < 200:
        raise RuntimeError("ChatGPT вернул пустой/слишком короткий план")

    project.general_plan = reply
    project.status = ProjectStatus.plan_ready
    await session.flush()

    # HITL: одобрение плана
    req = await send_hitl_text(
        bot, session, project,
        kind=HITLKind.approve_plan,
        title=f"Общий план ролика #{project.id}",
        text=reply,
        payload={"step": "plan"},
    )
    logger.info("[#{}] plan HITL={} отправлен", project.id, req.id)
