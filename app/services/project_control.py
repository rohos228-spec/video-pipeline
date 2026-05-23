"""Управление проектом: стоп, пауза, продолжение (как в Telegram-боте)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.node_registry import LINEAR_NODE_TYPES, RUNNING_TO_NODE_TYPE, WORK_NODES
from app.services.project_state import is_running_status
from app.services.step_cancel import request_stop


def _rollback_for_running(status: ProjectStatus) -> ProjectStatus:
    node_type = RUNNING_TO_NODE_TYPE.get(status)
    if not node_type or node_type not in LINEAR_NODE_TYPES:
        return ProjectStatus.new
    idx = LINEAR_NODE_TYPES.index(node_type)
    if idx <= 0:
        return ProjectStatus.new
    prev_type = LINEAR_NODE_TYPES[idx - 1]
    return WORK_NODES[prev_type].ready_status


async def stop_project_running(session: AsyncSession, project: Project) -> dict[str, str]:
    """⏹ Остановить текущий running-шаг."""
    request_stop(project.id)
    msg = "нет активного шага"
    if is_running_status(project.status):
        cur = project.status
        rollback_to = _rollback_for_running(cur)
        project.status = rollback_to
        project.auto_mode = False
        meta = dict(project.meta or {})
        meta.pop("enrich_auto_chain_to", None)
        project.meta = meta
        project.updated_at = datetime.utcnow()
        node_type = RUNNING_TO_NODE_TYPE.get(cur, "")
        label = WORK_NODES[node_type].node_type if node_type in WORK_NODES else cur.value
        msg = f"остановлен шаг «{label}» → {rollback_to.value}"
    elif project.status is not ProjectStatus.paused:
        project.auto_mode = False
        project.updated_at = datetime.utcnow()
        msg = "флаг остановки установлен, auto_mode выключен"
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
