"""Шаг 1–2: тема → общий план ролика (ChatGPT web + мастер-промт PLAN_SHORTS).
Затем HITL-одобрение в Telegram."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import HITLKind, Project, ProjectStatus
from app.services.hitl import send_hitl_text
from app.services.prompt_library import get_project_prompt
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.planning:
        return
    logger.info("[#{}] make_plan starting: '{}'", project.id, project.topic)

    master = get_project_prompt(project, "plan")
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

    try:
        _sheet_for_project(project).write_general(
            topic=project.topic,
            slug=project.slug,
            hero_mode=project.hero_mode,
            status=project.status.value,
            general_plan=reply,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet plan write failed: {}", project.id, e)

    # HITL: одобрение плана
    req = await send_hitl_text(
        bot, session, project,
        kind=HITLKind.approve_plan,
        title=f"Общий план ролика #{project.id}",
        text=reply,
        payload={"step": "plan"},
    )
    logger.info("[#{}] plan HITL={} отправлен", project.id, req.id)
