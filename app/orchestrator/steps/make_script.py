"""Шаг 2: общий план → закадровый текст (xlsx-flow, как Telegram _run_script_xlsx)."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLKind, Project, ProjectStatus
from app.services import xlsx_step_runners as xsr
from app.services.hitl import send_hitl_text
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.scripting:
        return
    from app.services.gen_queue_run import is_user_stopped

    if is_user_stopped(project):
        logger.info("[#{}] make_script: user_stop — не запускаем GPT", project.id)
        return
    logger.info("[#{}] make_script (xlsx-flow) starting", project.id)

    _result, voiceover_text = await xsr.run_script_xlsx(project)

    if len(voiceover_text) < 200:
        raise RuntimeError("ChatGPT вернул пустой/слишком короткий сценарий")

    project.script_text = voiceover_text
    project.status = ProjectStatus.script_ready
    await session.flush()

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet status write failed: {}", project.id, e)

    try:
        from app.services.node_xlsx_snapshot import find_node_key_for_type
        from app.services.storage_node import sync_downstream_storage_from_node

        script_key = await find_node_key_for_type(session, project, "script")
        if script_key:
            synced = sync_downstream_storage_from_node(project, script_key)
            for info in synced:
                logger.info(
                    "[#{}] make_script: storage {} ← {} copied={} skipped={}",
                    project.id,
                    info.get("storageNode"),
                    script_key,
                    info.get("copied"),
                    info.get("skipped"),
                )
            if synced:
                await session.flush()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] make_script: sync downstream storage failed: {}",
            project.id,
            e,
        )

    await send_hitl_text(
        bot,
        session,
        project,
        kind=HITLKind.approve_script,
        title=f"Закадровый текст #{project.id}",
        text=voiceover_text,
        payload={"step": "script"},
    )
