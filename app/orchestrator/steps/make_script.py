"""Шаг 3: общий план → сценарий озвучки.

В массовой генерации (batch sub'ы) — через xlsx-flow: прикладываем
`project.xlsx` + промт-файл, GPT возвращает `voiceover.txt`, бот его
сохраняет (то же что одиночный `_run_script_xlsx` в `app/telegram/bot.py`).
См. `app/services/xlsx_steps.py`.

Для одиночных проектов (если оркестратор всё-таки доходит до этого шага)
работает текстовый fallback ниже — отправляем `general_plan` текстом.
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import HITLKind, Project, ProjectStatus
from app.services.hitl import send_hitl_text
from app.services.prompt_library import get_project_prompt
from app.services.xlsx_steps import run_script_xlsx_step
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.scripting:
        return

    # Массовый sub → xlsx-flow (как одиночный через TG-меню).
    if project.batch_id is not None:
        await run_script_xlsx_step(session, project, bot)
        return

    if not project.general_plan:
        raise RuntimeError("general_plan пуст — нечего превращать в сценарий")
    logger.info("[#{}] make_script (text-only fallback) starting", project.id)

    master = get_project_prompt(project, "script")
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
        title=f"Закадровый текст #{project.id}",
        text=reply,
        payload={"step": "script"},
    )
