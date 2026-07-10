"""Управление проектом: стоп, пауза, продолжение (как в Telegram-боте)."""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services.mass_factory import mass_parent_id
from app.services.project_state import is_running_status
from app.services.gen_queue_run import is_user_stopped
from app.services.step_cancel import clear_stop, is_generation_active, is_stop_requested, request_stop
from app.services.xlsx_flow_locks import clear_xlsx_flow_locks
from app.telegram.menu import step_by_running_status


async def stop_project_running(
    session: AsyncSession, project: Project
) -> dict[str, str | bool | list[str] | None]:
    """⏹ Остановить текущий шаг — та же логика, что `on_project_stop_running` в боте."""
    request_stop(project.id)
    xlsx_stopped = clear_xlsx_flow_locks(project.id)

    ok = False
    stopped_kind: str | None = None
    step_title: str | None = None
    rollback_from: str | None = None
    rollback_to_val: str | None = None

    if is_running_status(project.status):
        ok = True
        stopped_kind = "running"
        cur = project.status
        rollback_from = cur.value
        step = step_by_running_status(cur)
        rollback_to = (
            step.requires
            if step is not None and step.requires is not None
            else ProjectStatus.new
        )
        rollback_to_val = rollback_to.value
        project.status = rollback_to
        meta = dict(project.meta or {})
        chain_to = meta.pop("enrich_auto_chain_to", None)
        if chain_to is not None:
            project.meta = meta
            logger.info(
                "[#{}] STOP: cleared enrich_auto_chain_to=#{}",
                project.id,
                chain_to,
            )
        project.updated_at = datetime.utcnow()
        step_title = step.title if step is not None else cur.value
        clear_stop(project.id)
        meta = dict(project.meta or {})
        meta["user_stop"] = True
        if mass_parent_id(project) is not None:
            meta["mass_lane_user_stop"] = True
            logger.info(
                "[#{}] STOP: mass lane paused (mass_lane_user_stop) until manual start",
                project.id,
            )
        project.meta = meta
        logger.info(
            "[#{}] STOP: auto_advance paused (user_stop) until manual step start",
            project.id,
        )
        logger.info(
            "[#{}] STOP: rolled back {} -> {} (auto_mode={} сохранён)",
            project.id,
            cur.value,
            rollback_to.value,
            project.auto_mode,
        )
        msg = (
            f"остановлен шаг «{step_title}» → {rollback_to.value}"
        )
    elif xlsx_stopped:
        ok = True
        stopped_kind = "xlsx"
        meta = dict(project.meta or {})
        meta["user_stop"] = True
        project.meta = meta
        project.updated_at = datetime.utcnow()
        msg = (
            f"остановлен xlsx-flow ({', '.join(xlsx_stopped)})"
        )
    else:
        msg = f"Нет активных шагов (статус: {project.status.value})."

    if ok:
        await session.flush()

    still_active = is_generation_active(project.id)
    if ok and is_user_stopped(project):
        logger.info(
            "[#{}] STOP: user_stop активен — воркер/auto_advance заблокированы до ▶",
            project.id,
        )
    return {
        "ok": ok,
        "message": msg,
        "stopped_kind": stopped_kind,
        "step_title": step_title,
        "rollback_from": rollback_from,
        "rollback_to": rollback_to_val,
        "generation_still_active": still_active,
        "xlsx_stopped": xlsx_stopped,
    }


async def pause_project(session: AsyncSession, project: Project) -> None:
    if project.status is ProjectStatus.paused:
        return
    meta = dict(project.meta or {})
    meta["paused_from_status"] = project.status.value
    project.meta = meta
    project.status = ProjectStatus.paused
    project.updated_at = datetime.utcnow()
    await session.flush()


async def resume_project(session: AsyncSession, project: Project) -> str:
    if project.status is not ProjectStatus.paused:
        return project.status.value
    meta = dict(project.meta or {})
    from_status = meta.pop("paused_from_status", None)
    project.meta = meta
    try:
        project.status = ProjectStatus(from_status) if from_status else ProjectStatus.new
    except ValueError:
        project.status = ProjectStatus.new
    project.updated_at = datetime.utcnow()
    await session.flush()
    return project.status.value
