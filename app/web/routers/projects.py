"""REST: /api/projects."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus
from app.services.default_project import default_auto_mode_for_new_project
from app.services.event_bus import publish_project_event
from app.services.project_steps import list_step_codes, start_step
from app.storage import ProjectSheet
from app.web.deps import get_session
from app.web.schemas import CreateProjectRequest, ProjectDetail, ProjectSummary

router = APIRouter(prefix="/projects", tags=["projects"])


def _slugify(s: str) -> str:
    """Простой кириллица-латиница slugifier (как в app/services)."""
    base = re.sub(r"[^\w\s-]", "", s.lower(), flags=re.UNICODE).strip()
    base = re.sub(r"[\s_-]+", "-", base, flags=re.UNICODE)
    # Транслит кириллицы — повторяем существующую логику из проекта.
    cyr = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    lat = ["a", "b", "v", "g", "d", "e", "yo", "zh", "z", "i", "y", "k", "l", "m", "n",
           "o", "p", "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "",
           "e", "yu", "ya"]
    table = dict(zip(cyr, lat))
    out_chars: list[str] = []
    for ch in base:
        out_chars.append(table.get(ch, ch))
    base = "".join(out_chars)
    base = re.sub(r"[^a-z0-9-]", "", base)
    base = re.sub(r"-+", "-", base).strip("-")
    if not base:
        base = "project"
    return base[:80]


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    session: AsyncSession = Depends(get_session),
) -> list[Project]:
    rows = (
        await session.execute(select(Project).order_by(Project.id.desc()))
    ).scalars().all()
    return list(rows)


@router.get("/steps/catalog")
async def steps_catalog() -> list[dict[str, str]]:
    """Каталог шагов пайплайна (для веб-UI без Telegram)."""
    return list_step_codes()


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> Project:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    return p


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    session: AsyncSession = Depends(get_session),
) -> Project:
    if not payload.topic or not payload.topic.strip():
        raise HTTPException(status_code=400, detail="topic is required")
    base_slug = _slugify(payload.topic)
    # Уникализируем slug.
    slug = base_slug
    suffix = 2
    while (
        await session.execute(select(Project).where(Project.slug == slug))
    ).scalar_one_or_none() is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    auto_mode = payload.auto_mode
    if not payload.auto_mode and default_auto_mode_for_new_project():
        auto_mode = True
    p = Project(
        slug=slug,
        topic=payload.topic.strip(),
        hero_mode=payload.hero_mode,
        status=ProjectStatus.new,
        auto_mode=auto_mode,
    )
    session.add(p)
    await session.flush()
    sheet = ProjectSheet(file_path=p.data_dir / "project.xlsx")
    sheet.ensure_initialized(project_id=p.id, slug=p.slug)
    sheet.write_general(
        topic=p.topic,
        slug=p.slug,
        hero_mode=p.hero_mode,
        status=p.status.value,
    )
    await session.commit()
    await session.refresh(p)
    await publish_project_event(p.id, event_type="project_created", payload={
        "slug": p.slug,
        "topic": p.topic,
    })
    return p


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    await session.delete(p)
    await session.commit()
    await publish_project_event(project_id, event_type="project_deleted")


@router.patch("/{project_id}", response_model=ProjectDetail)
async def patch_project(
    project_id: int,
    payload: dict,
    session: AsyncSession = Depends(get_session),
) -> Project:
    """Частичное обновление полей проекта (для inspector-панели)."""
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    ALLOWED = {
        "topic", "hero_mode", "general_plan", "hero_description", "script_text",
        "image_generator", "aspect_ratio", "image_resolution", "image_relax",
        "video_generator", "video_resolution", "video_relax",
        "hero_count", "hero_descriptions", "hero_variations",
        "hero_variation_modifiers",
        "item_descriptions", "item_variations",
        "enrich_slots_count", "prompt_overrides", "gpt_text_overrides",
        "auto_mode", "meta",
    }
    for k, v in payload.items():
        if k in ALLOWED:
            setattr(p, k, v)
    p.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(p)
    await publish_project_event(project_id, event_type="project_updated")
    return p


@router.get("/{project_id}/media-review")
async def media_review(
    project_id: int,
    kind: str = Query("images", pattern="^(images|videos)$"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Кадры с путями к последним scene_image / scene_video для визуального HITL."""
    artifact_kind = (
        ArtifactKind.scene_image if kind == "images" else ArtifactKind.scene_video
    )
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .options(selectinload(Frame.artifacts))
            .order_by(Frame.number.asc())
        )
    ).scalars().all()
    out: list[dict] = []
    for fr in frames:
        arts = [a for a in fr.artifacts if a.kind == artifact_kind]
        arts.sort(key=lambda a: a.id, reverse=True)
        art = arts[0] if arts else None
        out.append(
            {
                "frame_id": fr.id,
                "number": fr.number,
                "voiceover_text": fr.voiceover_text,
                "image_prompt": fr.image_prompt,
                "animation_prompt": fr.animation_prompt,
                "status": fr.status.value if hasattr(fr.status, "value") else str(fr.status),
                "artifact_uuid": art.uuid if art else None,
                "file_path": art.path if art else None,
                "preview_url": (
                    f"/api/files?path={art.path}" if art and art.path else None
                ),
            }
        )
    return out


@router.post("/{project_id}/steps/{step_code}/run", response_model=ProjectDetail)
async def run_project_step(
    project_id: int,
    step_code: str,
    session: AsyncSession = Depends(get_session),
) -> Project:
    """Запустить шаг: статус → running, воркер выполнит advance_project."""
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        await start_step(session, p, step_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    await session.refresh(p)
    await publish_project_event(
        project_id,
        event_type="project_updated",
        payload={"step": step_code, "status": p.status.value},
    )
    return p
