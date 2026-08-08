"""Группы нод (пресеты канваса): каталог + вставка в канвас проекта."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.event_bus import publish_project_event
from app.services.node_groups import insert_node_group, list_node_groups
from app.web.deps import get_session

router = APIRouter(tags=["node-groups"])


@router.get("/node-groups")
async def get_node_groups() -> list[dict[str, Any]]:
    """Каталог групп нод для палитры «+ Группа»."""
    return list_node_groups()


@router.post("/projects/{project_id}/canvas/groups/{group_id}")
async def add_group_to_canvas(
    project_id: int,
    group_id: str,
    session: AsyncSession = Depends(get_session),
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Вшить группу нод в канвас проекта (позиции, связи, промты, флаги)."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    after = str((body or {}).get("after") or "").strip() or None
    try:
        result = await insert_node_group(session, project, group_id, after=after)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await publish_project_event(
        project_id,
        event_type="project_updated",
        payload={"canvas_group_added": group_id},
    )
    return result
