"""REST: /api/hitl — HITL-решения от веб-UI.

Поддерживает три типа действий:
  approve / regenerate / reject / edit_prompt

Перекликается с TG-кнопками. Используем тот же HITLDecision и поля БД, поэтому
обоим UI хватает одной модели данных.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLDecision, HITLRequest
from app.services.event_bus import publish_hitl_event
from app.web.deps import get_session
from app.web.schemas import HITLDecisionRequest, HITLDTO

router = APIRouter(prefix="/hitl", tags=["hitl"])


@router.get("/pending", response_model=list[HITLDTO])
async def list_pending(
    session: AsyncSession = Depends(get_session),
) -> list[HITLRequest]:
    rows = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.decision == HITLDecision.pending)
            .order_by(HITLRequest.id.desc())
        )
    ).scalars().all()
    return list(rows)


@router.get("/project/{project_id}", response_model=list[HITLDTO])
async def list_for_project(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> list[HITLRequest]:
    rows = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.project_id == project_id)
            .order_by(HITLRequest.id.desc())
        )
    ).scalars().all()
    return list(rows)


@router.post("/{hitl_id}/decision", response_model=HITLDTO)
async def submit_decision(
    hitl_id: int,
    payload: HITLDecisionRequest,
    session: AsyncSession = Depends(get_session),
) -> HITLRequest:
    req = await session.get(HITLRequest, hitl_id)
    if req is None:
        raise HTTPException(status_code=404, detail="hitl request not found")
    if req.decision != HITLDecision.pending:
        raise HTTPException(status_code=400, detail="already decided")

    mapping = {
        "approve": HITLDecision.approved,
        "approved": HITLDecision.approved,
        "regenerate": HITLDecision.regenerate,
        "regen": HITLDecision.regenerate,
        "reject": HITLDecision.rejected,
        "rejected": HITLDecision.rejected,
        "edit_prompt": HITLDecision.edit_prompt,
    }
    new_decision = mapping.get(payload.decision)
    if new_decision is None:
        raise HTTPException(status_code=400, detail=f"unknown decision '{payload.decision}'")

    req.decision = new_decision
    req.decided_at = datetime.utcnow()
    if payload.edited_prompt is not None and new_decision == HITLDecision.edit_prompt:
        payload_dict = dict(req.payload or {})
        payload_dict["edited_prompt"] = payload.edited_prompt
        req.payload = payload_dict
    await session.commit()
    await session.refresh(req)

    await publish_hitl_event(
        req.project_id, req.id,
        event_type="hitl_decided",
        payload={"decision": new_decision.value, "kind": req.kind.value},
    )
    return req
