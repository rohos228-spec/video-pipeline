"""Шаг 1: тема → общий план (xlsx-flow через ChatGPT web).

Мастер-промт уходит файлом; в чат — только текст из gpt_text_overrides
или дефолтное сопр. сообщение. Затем HITL-одобрение в Telegram.
"""

from __future__ import annotations

from datetime import datetime

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
    if project.status is not ProjectStatus.planning:
        return
    logger.info("[#{}] make_plan (xlsx-flow) starting: '{}'", project.id, project.topic)

    sheet = _sheet_for_project(project)
    xlsx_path = sheet.ensure_initialized(project_id=project.id, slug=project.slug)
    if not xlsx_path.exists():
        raise RuntimeError(f"make_plan: project.xlsx не найден: {xlsx_path}")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_plan_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "plan", topic=project.topic, prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"plan_{ts}.xlsx"

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        await cx.ask_with_prompt_files(
            gpt,
            chat_msg,
            [prompt_file, xlsx_path],
            timeout=900,
            project_id=project.id,
            step_code="plan",
        )
        await cx.download_and_replace_xlsx(
            gpt, xlsx_path, downloaded, timeout=900
        )

    await cx.sync_project_xlsx(session, project, xlsx_path, keep_fields=False)

    plan_text = (project.general_plan or "").strip()
    if len(plan_text) < 200:
        raise RuntimeError(
            "ChatGPT вернул пустой/слишком короткий план после xlsx-sync"
        )

    project.status = ProjectStatus.plan_ready
    await session.flush()

    try:
        sheet.write_general(
            topic=project.topic,
            slug=project.slug,
            hero_mode=project.hero_mode,
            status=project.status.value,
            general_plan=plan_text,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet plan write failed: {}", project.id, e)

    req = await send_hitl_text(
        bot,
        session,
        project,
        kind=HITLKind.approve_plan,
        title=f"Общий план ролика #{project.id}",
        text=plan_text,
        payload={"step": "plan"},
    )
    logger.info("[#{}] plan HITL={} отправлен", project.id, req.id)
