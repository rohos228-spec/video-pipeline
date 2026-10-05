"""REST под /api/change-control. Чтение, создание и отклонение предложения.

Эндпоинтов одобрения и применения нет намеренно: применить и откатить можно
только планом слоя действий с подтверждением оператора (change.apply / change.rollback).
Автор предложения берётся из AuthDep, а не из тела запроса.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.services.access_guard import LocalOnlyDep
from app.services.action_layer import approval
from app.services.action_layer.context import ActionContext
from app.services.change_control import service, tiers
from app.services.change_control.errors import ChangeRefused
from app.services.change_control.gateway import CtxGateway
from app.web.auth_sessions import AuthDep

router = APIRouter(prefix="/change-control", tags=["change-control"])


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: str = Field(..., pattern=r"^T[0-3]$")
    kind: str = Field(..., min_length=1, max_length=24)
    rationale: str = Field(..., min_length=1, max_length=tiers.MAX_RATIONALE)
    candidate: dict
    project_id: int | None = Field(None, ge=1)
    node_key: str = Field("", max_length=120)


def _http(exc: ChangeRefused) -> HTTPException:
    return HTTPException(exc.http, detail={"code": exc.code, "message": exc.message,
                                           "errors": exc.errors})


@router.get("/allow-list")
async def allow_list(operator: AuthDep) -> dict:
    return tiers.describe_allow_list()


@router.get("/proposals")
async def list_proposals(operator: AuthDep, project_id: int | None = Query(None, ge=1),
                         status: str | None = Query(None, max_length=16),
                         tier: str | None = Query(None, pattern=r"^T[0-3]$"),
                         node_key: str | None = Query(None, max_length=120),
                         limit: int = Query(50, ge=1, le=200)) -> dict:
    return await service.list_proposals(project_id=project_id, status=status, tier=tier,
                                        node_key=node_key, limit=limit)


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, operator: AuthDep) -> dict:
    try:
        return await service.get_proposal(proposal_id)
    except ChangeRefused as exc:
        raise _http(exc) from exc


@router.post("/proposals", dependencies=[LocalOnlyDep])
async def create_proposal(request: Request, body: ProposalIn, operator: AuthDep) -> dict:
    who = approval.OperatorIdentity.from_token(operator).id
    gateway = CtxGateway(ActionContext(app=request.app, operator=who, read_only=True))
    try:
        return await service.propose(
            gateway, tier=body.tier, kind=body.kind, rationale=body.rationale,
            candidate=body.candidate, proposer=who, project_id=body.project_id,
            node_key=body.node_key)
    except ChangeRefused as exc:
        raise _http(exc) from exc


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=3, max_length=500)


@router.post("/proposals/{proposal_id}/reject", dependencies=[LocalOnlyDep])
async def reject_proposal(proposal_id: str, body: RejectIn, operator: AuthDep) -> dict:
    """Только закрывает открытое предложение; применить и изменить значение не может."""
    who = approval.OperatorIdentity.from_token(operator).id
    try:
        return await service.reject(proposal_id, body.reason, actor=who)
    except ChangeRefused as exc:
        raise _http(exc) from exc


@router.get("/prompt-base")
async def prompt_base(request: Request, operator: AuthDep,
                      project_id: int = Query(..., ge=1),
                      node_key: str = Query(..., min_length=1, max_length=120)) -> dict:
    who = approval.OperatorIdentity.from_token(operator).id
    gateway = CtxGateway(ActionContext(app=request.app, operator=who, read_only=True))
    try:
        return await service.prompt_base(gateway, project_id, node_key)
    except ChangeRefused as exc:
        raise _http(exc) from exc


@router.get("/versions")
async def list_versions(operator: AuthDep, target: str = Query(..., max_length=300),
                        limit: int = Query(100, ge=1, le=500)) -> dict:
    try:
        return await service.list_versions(target, limit=limit)
    except ChangeRefused as exc:
        raise _http(exc) from exc
