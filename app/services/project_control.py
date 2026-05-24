"""Управление проектом: стоп, пауза, продолжение (как в Telegram-боте)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services.project_state import is_running_status
from app.services.step_cancel import request_stop
from app.services.xlsx_flow_locks import clear_xlsx_flow_locks
from app.telegram.menu import step_by_running_status


async def stop_project_running(session: AsyncSession, project: Project) -> dict[str, str]:
    """⏹ Остановить текущий running-шаг — та же логика, что on_project_stop_running в боте."""
    request_stop(project.id)
    xlsx_stopped = clear_xlsx_flow_locks(project.id)
    msg = "флаг остановки установлен"
    if is_running_status(project.status):
        cur = project.status
        step = step_by_running_status(cur)
        rollback_to = (
            step.requires
            if step is not None and step.requires is not None
            else ProjectStatus.new
        )
        project.status = rollback_to
        project.auto_mode = False
        meta = dict(project.meta or {})
        meta.pop("enrich_auto_chain_to", None)
        project.meta = meta
        project.updated_at = datetime.utcnow()
        step_title = step.title if step is not None else cur.value
        msg = f"остановлен шаг «{step_title}» → {rollback_to.value}, auto_mode выключен"
    elif xlsx_stopped:
        msg = f"остановлен xlsx-flow ({', '.join(xlsx_stopped)}), auto_mode выключен"
        project.auto_mode = False
        project.updated_at = datetime.utcnow()
    elif project.status is not ProjectStatus.paused:
        project.auto_mode = False
        project.updated_at = datetime.utcnow()
        msg = "auto_mode выключен"
    await session.flush()
    return {"message": msg}


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
