"""API: папки сайдбара, порядок проектов, очередь генерации."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import sidebar_layout as layout_svc

router = APIRouter(prefix="/sidebar-layout", tags=["sidebar-layout"])


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class FolderRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectPlacement(BaseModel):
    folder_id: str | None = None
    order: int = 0


class LayoutUpdate(BaseModel):
    folders: list[dict] | None = None
    project_layout: dict[str, ProjectPlacement | dict] | None = None
    gen_queue: list[int] | None = None


class GenQueueToggle(BaseModel):
    project_id: int


@router.get("")
async def get_sidebar_layout() -> dict:
    from app.db import session_scope
    from app.models import Project
    from sqlalchemy import select

    async with session_scope() as session:
        ids = {
            int(p.id)
            for p in (await session.execute(select(Project.id))).scalars().all()
        }
    return layout_svc.layout_for_api(ids)


@router.put("")
async def put_sidebar_layout(body: LayoutUpdate) -> dict:
    project_layout = None
    if body.project_layout is not None:
        project_layout = {
            k: v.model_dump() if isinstance(v, ProjectPlacement) else dict(v)
            for k, v in body.project_layout.items()
        }
    layout_svc.update_layout(
        folders=body.folders,
        project_layout=project_layout,
        gen_queue=body.gen_queue,
    )
    from app.db import session_scope
    from app.models import Project
    from sqlalchemy import select

    async with session_scope() as session:
        ids = {
            int(p.id)
            for p in (await session.execute(select(Project.id))).scalars().all()
        }
    return layout_svc.layout_for_api(ids)


@router.post("/folders")
async def create_folder(body: FolderCreate) -> dict:
    try:
        return layout_svc.create_folder(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/folders/{folder_id}")
async def rename_folder(folder_id: str, body: FolderRename) -> dict:
    try:
        return layout_svc.rename_folder(folder_id, body.name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str) -> dict:
    if not layout_svc.delete_folder(folder_id):
        raise HTTPException(status_code=404, detail="folder not found")
    return {"ok": True}


@router.post("/gen-queue/toggle")
async def toggle_gen_queue(body: GenQueueToggle) -> dict:
    queue = layout_svc.toggle_gen_queue(body.project_id)
    positions = {pid: idx + 1 for idx, pid in enumerate(queue)}
    return {
        "gen_queue": queue,
        "gen_queue_positions": positions,
        "position": positions.get(body.project_id),
    }
