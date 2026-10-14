"""REST: /api/hitl — HITL-решения от веб-UI.

Поддерживает три типа действий:
  approve / regenerate / reject / edit_prompt

Перекликается с TG-кнопками. Используем тот же HITLDecision и поля БД, поэтому
обоим UI хватает одной модели данных.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLDecision, HITLKind, HITLRequest, Project
from app.project_db import get_project_sessionmaker
from app.services.event_bus import publish_hitl_event
from app.services.hitl_apply import apply_hitl_side_effects, claim_hitl_decision
from app.services.hitl_locator import (
    AMBIGUOUS_RU, HitlAmbiguous, hitl_session_scope, mirror_decision_to_master,
)
from app.settings import settings
from app.web.deps import get_session
from app.web.schemas import HITLDecisionRequest, HITLDTO

router = APIRouter(prefix="/hitl", tags=["hitl"])

PUBLISH_DECISION_ELSEWHERE_RU = (
    "Решение по публикации принимается только на странице публикаций или кнопкой в Telegram")


@router.get("/pending", response_model=list[HITLDTO])
async def list_pending(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[HITLRequest]:
    query = (select(HITLRequest).where(HITLRequest.decision == HITLDecision.pending)
             .order_by(HITLRequest.id.desc()))
    rows = list((await session.execute(query)).scalars().all())
    if get_session in request.app.dependency_overrides:
        return rows
    # Запросы проектов лежат в их project.db: собираем их вместе с запросами из master.
    # Проектная копия главнее master (одинаковые проект и номер - одна запись).
    merged: dict[tuple[int, int], HITLRequest] = {(r.project_id, r.id): r for r in rows}
    for project in (await session.execute(select(Project))).scalars().all():
        try:
            folder = Path(project.data_dir)
            if not (folder / "project.db").is_file():
                continue
            sessions = await get_project_sessionmaker(folder)
            async with sessions() as local:
                local_rows = (await local.execute(query.where(HITLRequest.project_id == project.id))).scalars().all()
        except (SQLAlchemyError, OSError) as exc:
            logger.warning("hitl pending: project #{} db unreadable: {}", project.id, exc)
            continue
        for r in local_rows:
            merged[(r.project_id, r.id)] = r
    return sorted(merged.values(), key=lambda r: (r.created_at or datetime.min, r.id), reverse=True)


@router.get("/project/{project_id}", response_model=list[HITLDTO])
async def list_for_project(
    project_id: int, session: AsyncSession = Depends(get_session),
    request: Request = None,
) -> list[HITLRequest]:
    # FastAPI injects Request; the default preserves legacy two-argument calls.
    query = (select(HITLRequest).where(HITLRequest.project_id == project_id)
             .order_by(HITLRequest.id.desc()))
    if request is not None and get_session in request.app.dependency_overrides:
        return list((await session.execute(query)).scalars().all())

    project = await session.get(Project, project_id)
    if project is None:
        return []
    raw_dir = project.data_dir
    if raw_dir is not None:
        try:
            root = Path(settings.data_dir).resolve()
            folder = Path(raw_dir).resolve()
            candidate = folder / "project.db"
            database = candidate.resolve()
            if not folder.is_relative_to(root) or not database.is_relative_to(root):
                raise HTTPException(status_code=400, detail="База проекта лежит вне папки данных")
            existing = candidate.exists() or candidate.is_symlink()
            if existing and not candidate.is_file():
                raise HTTPException(status_code=503, detail="База проекта недоступна")
        except (OSError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail="База проекта недоступна") from exc
        if existing:
            # Existing project storage is authoritative even with no HITL rows.
            # Never initialize, migrate or copy master rows during this GET.
            try:
                sessions = await get_project_sessionmaker(folder)
                async with sessions() as local:
                    owner = await local.get(Project, project_id)
                    if owner is None or owner.slug != project.slug:
                        raise HTTPException(status_code=503, detail="База проекта недоступна")
                    rows = (await local.execute(query)).scalars().all()
                return list(rows)
            except (SQLAlchemyError, OSError) as exc:
                raise HTTPException(status_code=503, detail="База проекта недоступна") from exc

    # Master-only legacy storage: no project file means no initialization.
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


_MAPPING = {
    "approve": HITLDecision.approved,
    "approved": HITLDecision.approved,
    "regenerate": HITLDecision.regenerate,
    "regen": HITLDecision.regenerate,
    "reject": HITLDecision.rejected,
    "rejected": HITLDecision.rejected,
    "edit_prompt": HITLDecision.edit_prompt,
}


def _actor(request: Request) -> str:
    from app.services.operator_audit import resolve_actor
    try:
        return resolve_actor(request.scope)
    except Exception:  # noqa: BLE001 - автор решения не должен ломать само решение
        return "operator"


async def _decide(session: AsyncSession, hitl_id: int, payload: HITLDecisionRequest,
                  project_id: int | None, actor: str) -> HITLRequest:
    req = await session.get(HITLRequest, hitl_id)
    if req is None or (project_id is not None and req.project_id != project_id):
        raise HTTPException(status_code=404, detail="Запрос подтверждения не найден")
    if req.kind is HITLKind.approve_publish:
        # Разрешение на публикацию выдаётся только с проверкой файла, подписи и площадки (страница публикаций
        # или кнопка в Telegram). Общая ручка не должна менять такой запрос в обход этих проверок.
        raise HTTPException(status_code=409, detail=PUBLISH_DECISION_ELSEWHERE_RU)
    if req.decision != HITLDecision.pending:
        raise HTTPException(status_code=400, detail="Решение по этому запросу уже принято")

    new_decision = _MAPPING.get(payload.decision)
    if new_decision is None:
        raise HTTPException(status_code=400, detail=f"Неизвестное решение «{payload.decision}»")

    # Занять запрос одним условным UPDATE: два одновременных решения не запишутся оба.
    if not await claim_hitl_decision(session, req, new_decision, actor=actor,
                                     edited_prompt=payload.edited_prompt):
        await session.rollback()
        raise HTTPException(status_code=400, detail="Решение по этому запросу уже принято")
    await apply_hitl_side_effects(session, req, new_decision, advance=payload.advance)
    await session.commit()
    await session.refresh(req)
    return req


@router.post("/{hitl_id}/decision", response_model=HITLDTO)
async def submit_decision(
    hitl_id: int,
    payload: HITLDecisionRequest,
    request: Request,
    project_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> HITLRequest:
    wanted = project_id if project_id is not None else payload.project_id
    actor = _actor(request)
    if get_session in request.app.dependency_overrides:
        req = await _decide(session, hitl_id, payload, wanted, actor)
    else:
        try:
            async with hitl_session_scope(hitl_id, wanted) as (scoped, location):
                req = await _decide(scoped, hitl_id, payload, wanted, actor)
        except HitlAmbiguous as exc:
            raise HTTPException(status_code=409, detail=AMBIGUOUS_RU) from exc
        if location is not None and location.store == "project":
            await mirror_decision_to_master(req)

    await publish_hitl_event(
        req.project_id, req.id,
        event_type="hitl_decided",
        payload={"decision": req.decision.value, "kind": req.kind.value},
    )
    return req
