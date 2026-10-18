"""REST карточки сценария NII 67."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db import commit_with_retry
from app.models import Project
from app.services.content_locks import lock_ui_field
from app.services.event_bus import publish_project_event
from app.services.gpt_client import GptApiUnavailable
from app.services.nii67_agent import (
    fix_camera_sequence,
    render_plan,
    validate_frame,
)
from app.services.nii67_card import (
    GENERATED_LIMIT,
    META_KEY,
    card_from_meta,
    card_from_payload,
    compile_scenario,
    default_card,
    plan_ready,
)
from app.services.nii67_scenario import ScenarioGenerationError, generate_plan, generate_scenarios
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
    meta = project.meta if isinstance(project.meta, dict) else {}
    frames = (meta.get("nii67_frames") or {}).get("frames") or []
    return {"card": card_from_meta(project.meta), "frames": frames}


@router.put("/{project_id}/nii67/frames")
async def put_nii67_frames(
    project_id: int, session: ProjectSession, payload: CardBody
) -> dict[str, Any]:
    """Сохранить отредактированные оператором кадры (контракт проверяется)."""
    project = _project_or_404(await session.get(Project, project_id))
    frames = payload.get("frames") if isinstance(payload, dict) else None
    if not isinstance(frames, list):
        raise HTTPException(status_code=400, detail="frames list required")
    frames = fix_camera_sequence([f for f in frames if isinstance(f, dict)])
    bad = {f.get("slot_id"): validate_frame(f) for f in frames if validate_frame(f)}
    if bad:
        raise HTTPException(status_code=400, detail={"invalid": bad})
    meta = project.meta if isinstance(project.meta, dict) else {}
    stored = dict(meta.get("nii67_frames") or {})
    stored["frames"] = frames
    apply_project_meta_patch(project, {"nii67_frames": stored}, source="nii67_frames")
    flag_modified(project, "meta")
    project.updated_at = datetime.utcnow()
    await commit_with_retry(session)
    return {"frames": frames}


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


_UNAVAILABLE = "Текстовая модель не настроена. Проверьте ключ vibecode у ноды «Сценарий»."


@router.post("/{project_id}/nii67/plan")
async def plan_nii67(
    project_id: int,
    session: ProjectSession,
    payload: CardBody,
) -> dict[str, Any]:
    """Этап 1: короткий редактируемый план двух дверей и финала."""
    project = _project_or_404(await session.get(Project, project_id))
    card = _read_card(payload)
    try:
        card["plan"] = await generate_plan(card, project=project)
    except ScenarioGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GptApiUnavailable as exc:
        raise HTTPException(status_code=422, detail=_UNAVAILABLE) from exc
    card = _stamp(card, "save")
    await _store(session, project, card)
    return {"card": card}


@router.post("/{project_id}/nii67/storyboard")
async def storyboard_nii67(
    project_id: int,
    session: ProjectSession,
    payload: CardBody,
) -> dict[str, Any]:
    """Этап 2: полная раскадровка строго по текущему тексту плана."""
    project = _project_or_404(await session.get(Project, project_id))
    card = _read_card(payload)
    plan = card.get("plan") or {}
    if not plan_ready(plan):
        raise HTTPException(status_code=400, detail="Сначала заполните план: мир и что происходит для обеих дверей.")
    try:
        card, agent = await generate_scenarios(card, project=project, plan=plan)
    except ScenarioGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GptApiUnavailable as exc:
        raise HTTPException(status_code=422, detail=_UNAVAILABLE) from exc
    card = _stamp(card, "save")
    text = (compile_scenario(card).strip() + "\n\n" + render_plan(agent))[:GENERATED_LIMIT]
    card["generated_text"] = text
    project.general_plan = text
    apply_project_meta_patch(
        project, {META_KEY: card, "nii67_frames": agent}, source="nii67_storyboard"
    )
    lock_ui_field(project, "general_plan")
    flag_modified(project, "meta")
    project.updated_at = datetime.utcnow()
    await commit_with_retry(session)
    await publish_project_event(
        project.id,
        event_type="project_updated",
        payload={"nii67": True, "general_plan": True},
    )
    return {"card": card, "general_plan": project.general_plan, "frames": agent["frames"]}


@router.post("/{project_id}/nii67/reset")
async def reset_nii67(project_id: int, session: ProjectSession) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    card = _stamp(default_card(), "save")
    await _store(session, project, card)
    return {"card": card}


_UPLOAD_SLOTS = {"intro_video", "portal_image", "portal_video"}
_UPLOAD_MAX = 500 * 1024 * 1024


@router.post("/{project_id}/nii67/upload")
async def upload_nii67(
    project_id: int,
    session: ProjectSession,
    slot: str = Query(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Сохранить файл карточки (исходное видео, фото/видео дверей) в папку проекта."""
    import re
    from pathlib import Path

    if slot not in _UPLOAD_SLOTS:
        raise HTTPException(status_code=400, detail="unknown slot")
    project = _project_or_404(await session.get(Project, project_id))
    if not project.data_dir:
        raise HTTPException(status_code=400, detail="project has no data_dir")
    name = re.sub(r"[^\w.\-]+", "_", Path(file.filename or "file").name)[:120] or "file"
    target_dir = Path(project.data_dir) / "nii67_assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{slot}__{name}"
    size = 0
    with (target_dir / stored).open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > _UPLOAD_MAX:
                out.close()
                (target_dir / stored).unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="file too large")
            out.write(chunk)
    return {"slot": slot, "name": stored, "path": f"nii67_assets/{stored}", "size": size}
