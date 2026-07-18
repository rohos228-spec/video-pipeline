"""Шаг 1: тема → общий план (xlsx-flow через ChatGPT web).

GPT-сессия и post-processing — `xlsx_step_runners` (как Telegram bot).
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLKind, Project, ProjectStatus
from app.services import xlsx_step_runners as xsr
from app.services.hitl import send_hitl_text
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.planning:
        return
    logger.info(
        "[#{}] make_plan (xlsx-flow, {}) starting: '{}'",
        project.id,
        xsr.XLSX_STEP_RUNNERS_ID,
        project.topic,
    )

    await xsr.run_plan_xlsx(project)
    proj_xlsx = project.data_dir / "project.xlsx"
    try:
        from app.services.node_xlsx_snapshot import snapshot_and_bind_node_xlsx

        await snapshot_and_bind_node_xlsx(
            session, project, node_type="plan"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] plan xlsx snapshot bind failed: {}", project.id, e)
    await xsr.sync_after_plan(session, project, proj_xlsx)

    plan_text = (project.general_plan or "").strip()
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
        title=f"Сценарий ролика #{project.id}",
        text=plan_text,
        payload={"step": "plan"},
    )
    logger.info("[#{}] plan HITL={} отправлен", project.id, req.id)
