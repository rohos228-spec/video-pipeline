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


def _set_user_stop_gate(project: Project) -> None:
    """Железный STOP: блок worker + auto_advance до ручного ▶."""
    meta = dict(project.meta or {})
    meta["user_stop"] = True
    if mass_parent_id(project) is not None:
        meta["mass_lane_user_stop"] = True
        logger.info(
            "[#{}] STOP: mass_lane_user_stop до ручного запуска",
            project.id,
        )
    project.meta = meta


def clear_user_stop_gate(project: Project) -> list[str]:
    """Снять user_stop (например при постановке в gen_queue)."""
    meta = dict(project.meta or {})
    cleared: list[str] = []
    if meta.pop("user_stop", None) is not None:
        cleared.append("user_stop")
    if meta.pop("mass_lane_user_stop", None) is not None:
        cleared.append("mass_lane_user_stop")
    if cleared:
        project.meta = meta
        logger.info("[#{}] cleared {}", project.id, ", ".join(cleared))
    return cleared


async def stop_project_running(
    session: AsyncSession, project: Project
) -> dict[str, str | bool | list[str] | None]:
    """⏹ STOP: откат running-шага и/или блок автопродвижения (user_stop)."""
    request_stop(project.id)
    from app.services.montage_board_montage_job import cancel_montage_job

    await cancel_montage_job(project.id)
    xlsx_stopped = clear_xlsx_flow_locks(project.id)

    ok = False
    stopped_kind: str | None = None
    step_title: str | None = None
    rollback_from: str | None = None
    rollback_to_val: str | None = None
    msg = ""

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
        from app.services.run_sync import stop_active_running_node

        await stop_active_running_node(session, project)
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
        step_title = step.title if step is not None else cur.value
        clear_stop(project.id)
        msg = f"остановлен шаг «{step_title}» → {rollback_to.value}"
        logger.info(
            "[#{}] STOP: rolled back {} -> {} (auto_mode={} сохранён)",
            project.id,
            cur.value,
            rollback_to.value,
            project.auto_mode,
        )
    elif xlsx_stopped:
        ok = True
        stopped_kind = "xlsx"
        msg = f"остановлен xlsx-flow ({', '.join(xlsx_stopped)})"
    else:
        ok = True
        stopped_kind = "gate"
        msg = (
            f"автопродвижение остановлено (статус: {project.status.value})"
        )

    _set_user_stop_gate(project)
    project.updated_at = datetime.utcnow()
    await session.flush()

    logger.info(
        "[#{}] STOP: user_stop активен — воркер/auto_advance/gen_queue заблокированы до ▶",
        project.id,
    )
    still_active = is_generation_active(project.id)
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


async def rollback_running_for_queue(
    session: AsyncSession,
    project: Project,
    *,
    reason: str,
) -> bool:
    """Откат running-шага для gen_queue (request_stop + FSM нод, без user_stop)."""
    if not is_running_status(project.status):
        return False
    from app.services.step_cancel import request_stop
    from app.services.run_sync import stop_active_running_node

    request_stop(project.id)
    await stop_active_running_node(session, project)
    step = step_by_running_status(project.status)
    rollback = (
        step.requires
        if step is not None and step.requires is not None
        else ProjectStatus.new
    )
    cur = project.status.value
    project.status = rollback
    project.updated_at = datetime.utcnow()
    await session.flush()
    logger.warning(
        "[#{}] gen_queue rollback ({}): {} → {}",
        project.id,
        reason,
        cur,
        rollback.value,
    )
    return True
