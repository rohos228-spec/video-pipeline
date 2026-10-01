"""REST карточки сценария NII 67."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db import commit_with_retry
from app.models import Project
from app.services.content_locks import lock_ui_field
from app.services.event_bus import publish_project_event
from app.services.nii67_card import (
    GENERATED_LIMIT,
    META_KEY,
    card_from_meta,
    card_from_payload,
    compile_scenario,
    default_card,
)
from app.services.project_meta import apply_project_meta_patch
from app.web.deps import get_project_session

router = APIRouter(prefix="/projects", tags=["nii67"])

ProjectSession = Annotated[AsyncSession, Depends(get_project_session)]
CardBody = Annotated[dict[str, Any], Body()]


def _project_or_404(project: Project | None) -> Project:
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _read_card(payload: Any) -> dict[str, Any]:
    try:
        return card_from_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _stamp(card: dict[str, Any], mode: str) -> dict[str, Any]:
    card["last_save"] = mode
    card["saved_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return card


async def _store(session: AsyncSession, project: Project, card: dict[str, Any]) -> None:
    apply_project_meta_patch(project, {META_KEY: card}, source="nii67")
    flag_modified(project, "meta")
    project.updated_at = datetime.utcnow()
    await commit_with_retry(session)
    await publish_project_event(
        project.id, event_type="project_updated", payload={"nii67": True}
    )


@router.get("/{project_id}/nii67")
async def get_nii67(project_id: int, session: ProjectSession) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    return {"card": card_from_meta(project.meta)}


@router.put("/{project_id}/nii67")
async def put_nii67(
    project_id: int,
    session: ProjectSession,
    payload: CardBody,
    mode: str = Query("save"),
) -> dict[str, Any]:
    if mode not in {"draft", "save"}:
        raise HTTPException(status_code=400, detail="mode must be draft or save")
    project = _project_or_404(await session.get(Project, project_id))
    card = _stamp(_read_card(payload), mode)
    await _store(session, project, card)
    return {"card": card}


@router.post("/{project_id}/nii67/preview")
async def preview_nii67(
    project_id: int,
    session: ProjectSession,
    payload: CardBody,
) -> dict[str, Any]:
    _project_or_404(await session.get(Project, project_id))
    card = _read_card(payload)
    return {"text": compile_scenario(card)}


@router.post("/{project_id}/nii67/generate")
async def generate_nii67(
    project_id: int,
    session: ProjectSession,
    payload: CardBody,
) -> dict[str, Any]:
    """Собрать сценарий из карточки и записать его в general_plan."""
    project = _project_or_404(await session.get(Project, project_id))
    card = _stamp(_read_card(payload), "save")
    text = compile_scenario(card).strip()[:GENERATED_LIMIT]
    card["generated_text"] = text
    project.general_plan = text
    apply_project_meta_patch(project, {META_KEY: card}, source="nii67_generate")
    lock_ui_field(project, "general_plan")
    flag_modified(project, "meta")
    project.updated_at = datetime.utcnow()
    await commit_with_retry(session)
    await publish_project_event(
        project.id,
        event_type="project_updated",
        payload={"nii67": True, "general_plan": True},
    )
    return {"card": card, "general_plan": project.general_plan}


@router.post("/{project_id}/nii67/reset")
async def reset_nii67(project_id: int, session: ProjectSession) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    card = _stamp(default_card(), "save")
    await _store(session, project, card)
    return {"card": card}
