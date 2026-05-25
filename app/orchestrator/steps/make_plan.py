"""Шаг 1: тема → общий план (xlsx-flow через ChatGPT web).

GPT-сессия — та же, что Telegram _run_plan_xlsx (xlsx_gpt_flow).
"""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLKind, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services import xlsx_gpt_flow as xgf
from app.services.hitl import send_hitl_text
from app.services.xlsx_versioning import backup_to_old, replace_with
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.planning:
        return
    logger.info("[#{}] make_plan (xlsx-flow) starting: '{}'", project.id, project.topic)

    proj_xlsx = project.data_dir / "project.xlsx"
    if not proj_xlsx.exists():
        sheet = _sheet_for_project(project)
        proj_xlsx = sheet.ensure_initialized(
            project_id=project.id, slug=project.slug
        )
    if not proj_xlsx.exists():
        raise RuntimeError(f"make_plan: project.xlsx не найден: {proj_xlsx}")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_plan_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "plan", topic=project.topic, prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"plan_{ts}.xlsx"

    async def _do() -> None:
        await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx],
            downloaded,
            project_id=project.id,
            validate_xlsx_download=True,
        )
        backup_to_old(proj_xlsx)
        replace_with(proj_xlsx, downloaded)

    await xgf.run_under_xlsx_lock(project.id, "plan", _do)

    await cx.sync_project_xlsx(session, project, proj_xlsx, keep_fields=False)

    plan_text = (project.general_plan or "").strip()
    if len(plan_text) < 200:
        raise RuntimeError(
            "ChatGPT вернул пустой/слишком короткий план после xlsx-sync"
        )

    project.status = ProjectStatus.plan_ready
    await session.flush()

    try:
        _sheet_for_project(project).write_general(
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
