"""Группы нод (пресеты канваса): каталог, CRUD и вставка в канвас проекта."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.event_bus import publish_project_event
from app.services.node_groups import (
    delete_custom_group,
    get_node_group,
    group_from_canvas,
    insert_node_group,
    list_node_groups,
    save_custom_group,
    update_custom_group,
)
from app.web.deps import get_session

router = APIRouter(tags=["node-groups"])


def _summary(group_id: str) -> dict[str, Any]:
    for g in list_node_groups():
        if g["id"] == group_id:
            return g
    return {"id": group_id}


@router.get("/node-groups")
async def get_node_groups() -> list[dict[str, Any]]:
    """Каталог групп нод для палитры: встроенные + пользовательские."""
    return list_node_groups()


@router.post("/node-groups")
async def create_node_group(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Создать пользовательскую группу из сырого spec'а (JSON в node_groups/)."""
    try:
        group = save_custom_group(dict(body))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _summary(group.group_id)


@router.put("/node-groups/{group_id}")
async def patch_node_group(
    group_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Переименовать / сменить описание / категорию пользовательской группы."""
    patch = {
        k: body.get(k) for k in ("title", "description", "category") if k in body
    }
    if not patch:
        raise HTTPException(status_code=400, detail="пустой patch")
    try:
        group = update_custom_group(group_id, patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _summary(group.group_id)


@router.delete("/node-groups/{group_id}")
async def remove_node_group(group_id: str) -> dict[str, Any]:
    """Удалить пользовательскую группу (встроенные не удаляются)."""
    try:
        delete_custom_group(group_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"deleted": group_id}


@router.post("/projects/{project_id}/node-groups/from-selection")
async def create_group_from_selection(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Сохранить выделенные ноды канваса как пользовательскую группу.

    В группу уходят типы нод, подписи, промпт-варианты, конфиги «Работы
    с GPT», относительные позиции и связи внутри выделения — но не
    результаты работ.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    node_ids = body.get("node_ids")
    if not isinstance(node_ids, list) or not node_ids:
        raise HTTPException(status_code=400, detail="node_ids: пустой список")
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title пуст")
    try:
        group = await group_from_canvas(
            session,
            project,
            [str(i) for i in node_ids],
            title=title,
            description=str(body.get("description") or ""),
            category=str(body.get("category") or "planning"),
            group_id=(str(body["group_id"]) if body.get("group_id") else None),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _summary(group.group_id)


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
    if get_node_group(group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
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
