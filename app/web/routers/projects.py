"""REST: /api/projects."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services.event_bus import publish_project_event
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
    p = Project(
        slug=slug,
        topic=payload.topic.strip(),
        hero_mode=payload.hero_mode,
        status=ProjectStatus.new,
        auto_mode=payload.auto_mode,
    )
    session.add(p)
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
        "auto_mode",
    }
    for k, v in payload.items():
        if k in ALLOWED:
            setattr(p, k, v)
    p.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(p)
    await publish_project_event(project_id, event_type="project_updated")
    return p
