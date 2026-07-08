"""Управление проектом: стоп, пауза, продолжение (как в Telegram-боте)."""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services.mass_factory import mass_parent_id
from app.services.project_state import is_running_status
from app.services.step_cancel import (
    clear_stop,
    is_advance_active,
    is_stop_requested,
    request_stop,
)
from app.services.xlsx_flow_locks import (
    XLSX_FLOW_STEP_CODES,
    clear_xlsx_flow_locks,
    is_xlsx_flow_active,
)
from app.telegram.menu import step_by_running_status


def _set_user_stop_meta(project: Project) -> None:
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


def _generation_tasks_active(project_id: int) -> bool:
    """Активные asyncio-задачи (advance / xlsx / fleet), без учёта stop-файла."""
    if is_advance_active(project_id):
        return True
    if any(is_xlsx_flow_active(project_id, code) for code in XLSX_FLOW_STEP_CODES):
        return True
    try:
        from app.fleet.transfer_state import is_transfer_running

        if is_transfer_running(project_id):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _maybe_clear_stop_flag(project_id: int) -> None:
    """Снять stop-файл только когда нечего кооперативно прерывать."""
    if _generation_tasks_active(project_id):
        return
    clear_stop(project_id)


async def stop_project_running(
    session: AsyncSession, project: Project
) -> dict[str, str | bool | list[str] | None]:
    """⏹ Остановить текущий шаг — та же логика, что `on_project_stop_running` в боте."""
    request_stop(project.id)
    xlsx_stopped = clear_xlsx_flow_locks(project.id)

    from app.fleet.transfer_state import cancel_fleet_transfer

    fleet_stopped = await cancel_fleet_transfer(project.id)

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
        _set_user_stop_meta(project)
        logger.info(
            "[#{}] STOP: rolled back {} -> {} (auto_mode={} сохранён)",
            project.id,
            cur.value,
            rollback_to.value,
            project.auto_mode,
        )
        msg = f"остановлен шаг «{step_title}» → {rollback_to.value}"
    elif fleet_stopped:
        ok = True
        stopped_kind = "fleet_transfer"
        _set_user_stop_meta(project)
        project.updated_at = datetime.utcnow()
        msg = "остановлена отправка/скачивание fleet bundle"
    elif _generation_tasks_active(project.id) or is_stop_requested(project.id):
        ok = True
        stopped_kind = "auto_pipeline"
        _set_user_stop_meta(project)
        project.updated_at = datetime.utcnow()
        gen_active = _generation_tasks_active(project.id)
        logger.info(
            "[#{}] STOP: auto_pipeline (status={}, gen_active={})",
            project.id,
            project.status.value,
            gen_active,
        )
        msg = "остановлен фоновый шаг (ожидание завершения задачи…)"
    elif xlsx_stopped:
        ok = True
        stopped_kind = "xlsx"
        _set_user_stop_meta(project)
        project.updated_at = datetime.utcnow()
        msg = f"остановлен xlsx-flow ({', '.join(xlsx_stopped)})"
    else:
        msg = f"Нет активных шагов (статус: {project.status.value})."

    if ok:
        await session.flush()

    _maybe_clear_stop_flag(project.id)
    still_active = _generation_tasks_active(project.id)
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
