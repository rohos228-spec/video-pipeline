"""Чтение состояния и возобновление медиа-задач провайдера по уже известному task id.

Оба эндпоинта не отправляют ни одного нового платного запроса: чтение только смотрит журнал задач и
файлы; возобновление спрашивает провайдера про известную задачу и скачивает готовый результат внутри
режима «только возобновление» (``media_spend.resume_only``). Повторная платная отправка здесь невозможна.
Тот же сервис (``media_admission``) использует надзор (билет восстановления); эти эндпоинты - для человека
и для действий слоя действий ``media.get_recovery_assessment`` и ``media.resume_poll``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services import kill_switch, media_admission, media_recovery
from app.services.project_state import is_running_status
from app.web.deps import get_session

router = APIRouter(prefix="/projects/{project_id}/media-recovery", tags=["media-recovery"])


async def _project(session: AsyncSession, project_id: int) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _node(node_type: str) -> str:
    if node_type not in media_recovery.RECOVERY_PROFILES:
        raise HTTPException(status_code=404, detail=f"нет профиля восстановления для {node_type!r}")
    return node_type


def _keys(report: dict) -> list[dict]:
    return [{"key": k.get("key"), "mode": k["mode"], "code": k["code"], "task_id": k.get("task_id"),
             "provider": k.get("provider")} for k in report.get("keys", [])][:50]


@router.get("/{node_type}")
async def get_assessment(project_id: int, node_type: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Что можно безопасно сделать с медиа-нодой без новой оплаты. Только чтение."""
    project = await _project(session, project_id)
    node_type = _node(node_type)
    report = await media_recovery.assess(project, node_type)
    admission, denied = None, None
    try:
        admission = (await media_admission.admit(project, node_type))["operation"]
    except media_admission.MediaAdmissionDenied as exc:
        denied = exc.code
    return {"project_id": project_id, "node_type": node_type, "verdict": report["verdict"],
            "keys": _keys(report), "ledger": report.get("ledger"), "retry_same_max": report["retry_same_max"],
            "supervisor_admission": {"admitted_node": media_admission.admitted(node_type),
                                     "operation": admission, "denied": denied},
            "submits_new_paid_request": False}


@router.post("/{node_type}/resume-poll")
async def resume_poll(project_id: int, node_type: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Опросить известную задачу Kie и скачать готовое. Новых платных запросов нет (проверяется по журналу)."""
    project = await _project(session, project_id)
    node_type = _node(node_type)
    if not media_admission.admitted(node_type):
        raise HTTPException(status_code=409, detail={"code": "node_not_admitted", "node_type": node_type})
    if await kill_switch.is_active(session):
        raise HTTPException(status_code=409, detail={"code": "kill_switch_active"})
    if is_running_status(project.status):
        raise HTTPException(status_code=409, detail={"code": "project_busy", "status": project.status.value})
    try:
        admission = await media_admission.admit(project, node_type)
    except media_admission.MediaAdmissionDenied as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    if admission["operation"] != "resume_poll":
        raise HTTPException(status_code=409, detail={"code": "not_a_provider_resume",
                                                     "operation": admission["operation"]})
    outcome = await media_admission.run_and_verify(project, node_type, admission)
    return {"project_id": project_id, "node_type": node_type, **outcome}
