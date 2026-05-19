"""Шаг 3: общий план → сценарий озвучки.

В массовой генерации (batch sub'ы) — через xlsx-flow: прикладываем
`project.xlsx` + промт-файл, GPT возвращает `voiceover.txt`, бот его
сохраняет (то же что одиночный `_run_script_xlsx` в `app/telegram/bot.py`).
См. `app/services/xlsx_steps.py`.

Для одиночных проектов (если оркестратор всё-таки доходит до этого шага)
работает текстовый fallback ниже — отправляем `general_plan` текстом.

(Фаза 2) После генерации сценария — GPT-проверка через
`gpt_check_text_artifact`, ретраи до 3 раз при `regenerate`.
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import HITLKind, Project, ProjectStatus
from app.services.gpt_check import (
    GptCheckDecision,
    gpt_check_text_artifact,
    load_check_prompt,
)
from app.services.hitl import send_hitl_text
from app.services.prompt_library import get_project_prompt
from app.services.xlsx_steps import run_script_xlsx_step
from app.storage import for_project as _sheet_for_project

MAX_GPT_CHECK_RETRIES = 3


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

    reply: str | None = None
    for attempt in range(1, MAX_GPT_CHECK_RETRIES + 1):
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            reply = await gpt.ask_fresh(full_prompt, timeout=420)

            if not reply or len(reply) < 200:
                raise RuntimeError("ChatGPT вернул пустой сценарий")

            # (Фаза 2) GPT-проверка сценария.
            try:
                check_prompt = load_check_prompt("script")
            except FileNotFoundError:
                logger.warning("[#{}] промт проверки сценария не найден, пропускаю GPT-check", project.id)
                break

            check_result = await gpt_check_text_artifact(
                chatgpt_bot=gpt,
                check_prompt=check_prompt,
                artifact_text=reply,
                new_conversation=True,
                timeout=1200.0,
                download_replacement_to=project.data_dir / "tmp_gpt" / "script_replaced.txt",
            )
            logger.info(
                "[#{}] script GPT-check attempt {}/{}: decision={}",
                project.id, attempt, MAX_GPT_CHECK_RETRIES,
                check_result.decision.value,
            )

            if check_result.decision is GptCheckDecision.approved:
                break

            if check_result.decision is GptCheckDecision.replace_artifact:
                if check_result.replaced_path and check_result.replaced_path.exists():
                    replaced_text = check_result.replaced_path.read_text(encoding="utf-8").strip()
                    if len(replaced_text) >= 200:
                        reply = replaced_text
                        logger.info("[#{}] script: GPT прислал замену ({} chars)", project.id, len(replaced_text))
                break

            if check_result.decision is GptCheckDecision.regenerate:
                if attempt < MAX_GPT_CHECK_RETRIES:
                    logger.info(
                        "[#{}] script: GPT просит перегенерацию (hint: {}), retry {}/{}",
                        project.id, check_result.hint[:100],
                        attempt, MAX_GPT_CHECK_RETRIES,
                    )
                    continue
                logger.warning(
                    "[#{}] script: {} ретраев исчерпано, оставляем последний вариант",
                    project.id, MAX_GPT_CHECK_RETRIES,
                )
                break

            # timeout / parse_error
            break

    project.script_text = reply
    project.status = ProjectStatus.script_ready
    await session.flush()

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
