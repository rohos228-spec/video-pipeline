"""Шаг 1–2: тема → общий план ролика.

В массовой генерации (batch sub'ы) идёт через xlsx-flow — в чат ChatGPT
прикладывается `project.xlsx` и промт-файл, GPT возвращает обновлённый
xlsx, бот его подменяет (то же самое, что для одиночной кнопки
«Шаг 1 → План» в TG-меню). См. `app/services/xlsx_steps.py`.

Для одиночных проектов сохраняется старая ветка с текстовым промтом и
HITL-одобрением: пользователь обычно гонит одиночный через TG-меню,
которое запускает свой xlsx-flow напрямую через `_run_plan_xlsx`. Если же
оркестратор всё-таки доходит до этого шага у одиночного (auto_mode=True),
работает текстовый fallback ниже.

(Фаза 2) После генерации плана — GPT-проверка через `gpt_check_text_artifact`,
ретраи до 3 раз при `regenerate`, подтверждение при `approved`.
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
from app.services.xlsx_steps import run_plan_xlsx_step
from app.storage import for_project as _sheet_for_project

MAX_GPT_CHECK_RETRIES = 3


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.planning:
        return

    # Массовый sub → xlsx-flow (как одиночный через TG-меню).
    if project.batch_id is not None:
        await run_plan_xlsx_step(session, project, bot)
        return

    logger.info("[#{}] make_plan (text-only fallback) starting: '{}'", project.id, project.topic)

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

    reply: str | None = None
    for attempt in range(1, MAX_GPT_CHECK_RETRIES + 1):
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            if reply is None:
                reply = await gpt.ask_fresh(full_prompt, timeout=420)
            else:
                reply = await gpt.ask_fresh(full_prompt, timeout=420)

            if not reply or len(reply) < 200:
                raise RuntimeError("ChatGPT вернул пустой/слишком короткий план")

            # (Фаза 2) GPT-проверка плана.
            try:
                check_prompt = load_check_prompt("plan")
            except FileNotFoundError:
                logger.warning("[#{}] промт проверки плана не найден, пропускаю GPT-check", project.id)
                break

            check_result = await gpt_check_text_artifact(
                chatgpt_bot=gpt,
                check_prompt=check_prompt,
                artifact_text=reply,
                new_conversation=True,
                timeout=1200.0,
                download_replacement_to=project.data_dir / "tmp_gpt" / "plan_replaced.txt",
            )
            logger.info(
                "[#{}] plan GPT-check attempt {}/{}: decision={}",
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
                        logger.info("[#{}] plan: GPT прислал замену ({} chars)", project.id, len(replaced_text))
                break

            if check_result.decision is GptCheckDecision.regenerate:
                if attempt < MAX_GPT_CHECK_RETRIES:
                    logger.info(
                        "[#{}] plan: GPT просит перегенерацию (hint: {}), retry {}/{}",
                        project.id, check_result.hint[:100],
                        attempt, MAX_GPT_CHECK_RETRIES,
                    )
                    reply = None
                    continue
                logger.warning(
                    "[#{}] plan: {} ретраев исчерпано, оставляем последний вариант",
                    project.id, MAX_GPT_CHECK_RETRIES,
                )
                break

            # timeout / parse_error — оставляем как есть
            break

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
