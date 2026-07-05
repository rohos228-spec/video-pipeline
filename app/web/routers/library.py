"""REST API for the local versioned prompt library."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LibraryConfig, LibraryEvent, LibraryItem, LibraryVersion, Project
from app.services import local_library as lib
from app.web.deps import get_session

router = APIRouter(prefix="/library", tags=["library"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class LibraryItemDTO(BaseModel):
    id: int
    kind: str
    key: str
    title: str
    file_path: str
    active_version: int
    content_hash: str
    meta: dict[str, Any]
    created_at: str
    updated_at: str


class LibraryItemDetailDTO(LibraryItemDTO):
    content: str


class LibraryVersionDTO(BaseModel):
    id: int
    item_id: int
    version: int
    content_hash: str
    message: str | None
    author: str | None
    source: str | None
    file_path: str
    meta: dict[str, Any]
    created_at: str


class LibraryEventDTO(BaseModel):
    id: int
    item_id: int | None
    event_type: str
    payload: dict[str, Any]
    created_at: str


class LibraryConfigDTO(BaseModel):
    id: int
    name: str
    project_id: int | None
    snapshot: dict[str, Any]
    content_hash: str
    meta: dict[str, Any]
    created_at: str


class LibraryItemPayload(BaseModel):
    kind: str = Field(..., min_length=1, max_length=40)
    key: str | None = None
    title: str | None = None
    file_path: str | None = None
    content: str = ""
    message: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class LibraryItemPatch(BaseModel):
    title: str | None = None
    content: str
    message: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class SaveConfigPayload(BaseModel):
    name: str | None = None
    project_id: int | None = None
    snapshot: dict[str, Any] | None = None


class SavePromptBundlePayload(BaseModel):
    project_id: int | None = None
    step_id: str | None = None
    step_code: str | None = None
    node_type: str | None = None
    source_name: str | None = None
    title: str | None = None
    source_prompt: str | None = None
    processed_prompt: str | None = None
    blocks: list[dict[str, Any]] | None = None


def _item_dto(item: LibraryItem) -> LibraryItemDTO:
    return LibraryItemDTO(
        id=item.id,
        kind=item.kind,
        key=item.key,
        title=item.title,
        file_path=item.file_path,
        active_version=item.active_version,
        content_hash=item.content_hash,
        meta=item.meta or {},
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


def _version_dto(v: LibraryVersion) -> LibraryVersionDTO:
    return LibraryVersionDTO(
        id=v.id,
        item_id=v.item_id,
        version=v.version,
        content_hash=v.content_hash,
        message=v.message,
        author=v.author,
        source=v.source,
        file_path=v.file_path,
        meta=v.meta or {},
        created_at=v.created_at.isoformat(),
    )


def _event_dto(e: LibraryEvent) -> LibraryEventDTO:
    return LibraryEventDTO(
        id=e.id,
        item_id=e.item_id,
        event_type=e.event_type,
        payload=e.payload or {},
        created_at=e.created_at.isoformat(),
    )


def _config_dto(c: LibraryConfig) -> LibraryConfigDTO:
    return LibraryConfigDTO(
        id=c.id,
        name=c.name,
        project_id=c.project_id,
        snapshot=c.snapshot or {},
        content_hash=c.content_hash,
        meta=c.meta or {},
        created_at=c.created_at.isoformat(),
    )


@router.get("/items", response_model=list[LibraryItemDTO])
async def list_library_items(
    session: SessionDep,
    kind: str | None = None,
    q: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[LibraryItemDTO]:
    return [_item_dto(i) for i in await lib.list_items(session, kind=kind, q=q, limit=limit)]


@router.post("/items", response_model=LibraryItemDetailDTO)
async def create_library_item(
    payload: LibraryItemPayload,
    session: SessionDep,
) -> LibraryItemDetailDTO:
    file_path = payload.file_path or payload.key
    if not file_path:
        safe_title = (payload.title or "untitled").strip().replace("\\", "_").replace("/", "_")
        suffix = ".json" if payload.kind in {"style", "step_preset", "config"} else ".md"
        file_path = f"prompts/custom/{safe_title}{suffix}"
    key = payload.key or file_path
    item, _version, _changed = await lib.create_or_update_item(
        session,
        kind=payload.kind,
        key=key,
        title=payload.title or key,
        file_path=file_path,
        content=payload.content,
        message=payload.message or "created from Studio",
        author="studio",
        source="api",
        meta=payload.meta,
        force_version=False,
    )
    await session.commit()
    active = await lib.get_active_version(session, item)
    return LibraryItemDetailDTO(**_item_dto(item).model_dump(), content=active.content if active else "")


@router.get("/items/{item_id}", response_model=LibraryItemDetailDTO)
async def get_library_item(
    item_id: int,
    session: SessionDep,
) -> LibraryItemDetailDTO:
    item = await lib.get_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="library item not found")
    version = await lib.get_active_version(session, item)
    return LibraryItemDetailDTO(**_item_dto(item).model_dump(), content=version.content if version else "")


@router.put("/items/{item_id}", response_model=LibraryItemDetailDTO)
async def update_library_item(
    item_id: int,
    payload: LibraryItemPatch,
    session: SessionDep,
) -> LibraryItemDetailDTO:
    item = await lib.get_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="library item not found")
    item, version, _changed = await lib.create_or_update_item(
        session,
        kind=item.kind,
        key=item.key,
        title=payload.title or item.title,
        file_path=item.file_path,
        content=payload.content,
        message=payload.message or "edited from Studio",
        author="studio",
        source="api",
        meta=payload.meta,
        force_version=True,
    )
    await session.commit()
    return LibraryItemDetailDTO(**_item_dto(item).model_dump(), content=version.content)


@router.get("/items/{item_id}/versions", response_model=list[LibraryVersionDTO])
async def get_library_versions(
    item_id: int,
    session: SessionDep,
) -> list[LibraryVersionDTO]:
    if await lib.get_item(session, item_id) is None:
        raise HTTPException(status_code=404, detail="library item not found")
    return [_version_dto(v) for v in await lib.list_versions(session, item_id)]


@router.post("/items/{item_id}/restore/{version}", response_model=LibraryItemDetailDTO)
async def restore_library_version(
    item_id: int,
    version: int,
    session: SessionDep,
) -> LibraryItemDetailDTO:
    try:
        item, restored = await lib.restore_version(session, item_id, version, author="studio")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await session.commit()
    return LibraryItemDetailDTO(**_item_dto(item).model_dump(), content=restored.content)


@router.get("/items/{item_id}/download")
async def download_library_item(
    item_id: int,
    session: SessionDep,
) -> FileResponse:
    item = await lib.get_item(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="library item not found")
    path = lib.materialized_file_path(item)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="materialized file not found")
    await lib.log_event(session, "downloaded", item=item, payload={"file_path": item.file_path})
    await session.commit()
    return FileResponse(path=str(path), filename=path.name)


@router.get("/events", response_model=list[LibraryEventDTO])
async def list_library_events(
    session: SessionDep,
    item_id: int | None = None,
    event_type: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[LibraryEventDTO]:
    return [
        _event_dto(e)
        for e in await lib.list_events(session, item_id=item_id, event_type=event_type, limit=limit)
    ]


@router.get("/configs", response_model=list[LibraryConfigDTO])
async def list_library_configs(
    session: SessionDep,
    project_id: int | None = None,
) -> list[LibraryConfigDTO]:
    return [_config_dto(c) for c in await lib.list_configs(session, project_id=project_id)]


@router.post("/configs/save", response_model=LibraryConfigDTO)
async def save_library_config(
    payload: SaveConfigPayload,
    session: SessionDep,
) -> LibraryConfigDTO:
    if payload.snapshot is not None:
        cfg = await lib.save_config(
            session,
            name=payload.name or "manual-config",
            project_id=payload.project_id,
            snapshot=payload.snapshot,
            meta={"source": "api"},
        )
    else:
        if payload.project_id is None:
            raise HTTPException(status_code=400, detail="project_id or snapshot required")
        project = await session.get(Project, payload.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        cfg = await lib.save_project_config(session, project=project, name=payload.name)
    await session.commit()
    return _config_dto(cfg)


@router.post("/configs/{config_id}/apply/{project_id}", response_model=dict[str, Any])
async def apply_library_config(
    config_id: int,
    project_id: int,
    session: SessionDep,
) -> dict[str, Any]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        await lib.apply_config_to_project(session, config_id=config_id, project=project)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await session.commit()
    return {"ok": True, "project_id": project.id, "config_id": config_id}


@router.post("/prompt-bundles/save", response_model=dict[str, Any])
async def save_prompt_bundle(
    payload: SavePromptBundlePayload,
    session: SessionDep,
) -> dict[str, Any]:
    from app.services.prompt_composer import (
        STEP_CODE_TO_COMPOSE,
        compose_for_node_type,
        compose_step,
        compose_step_sections,
        merge_project_prompt_config,
        read_step_template,
    )
    from app.services.prompt_library import read_prompt

    project: Project | None = None
    if payload.project_id is not None:
        project = await session.get(Project, payload.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")

    step_id = payload.step_id
    if step_id is None and payload.step_code:
        step_id = STEP_CODE_TO_COMPOSE.get(payload.step_code)

    overrides = dict(project.prompt_overrides or {}) if project else {}
    hero_description = (
        (project.hero_descriptions or [None])[0]
        if project and isinstance(project.hero_descriptions, list)
        else None
    )
    topic = project.topic if project else None

    source_prompt = payload.source_prompt
    source_path: str | None = None
    if source_prompt is None and payload.step_code and payload.source_name:
        source_prompt = read_prompt(payload.step_code, payload.source_name)
        source_path = f"prompts/{payload.step_code}/{payload.source_name}.md"
    if source_prompt is None and step_id:
        source_prompt = read_step_template(step_id)
        source_path = f"prompts/steps/{step_id}/template.md"
    if source_prompt is None:
        source_prompt = ""

    blocks_map, vars_ = merge_project_prompt_config(
        overrides,
        hero_description=hero_description,
        topic=topic,
    )
    processed_prompt = payload.processed_prompt
    if processed_prompt is None:
        if step_id:
            processed_prompt = compose_step(step_id, blocks_map, vars_)
        elif payload.node_type:
            processed_prompt = compose_for_node_type(
                payload.node_type,
                overrides,
                hero_description=hero_description,
                topic=topic,
            )
        else:
            raise HTTPException(status_code=400, detail="step_id, step_code or node_type required")

    block_sections = payload.blocks
    if block_sections is None:
        block_sections = compose_step_sections(step_id, blocks_map) if step_id else []

    bundle_key = payload.title or f"{project.slug if project else 'manual'}-{step_id or payload.step_code or payload.node_type or 'prompt'}"
    saved = await lib.save_prompt_bundle(
        session,
        bundle_key=bundle_key,
        title=payload.title or bundle_key,
        source_prompt=source_prompt,
        processed_prompt=processed_prompt,
        blocks=block_sections,
        source_path=source_path,
        project_id=project.id if project else None,
        step_id=step_id,
        step_code=payload.step_code,
    )
    await session.commit()
    return {
        "ok": True,
        "items": {
            key: [_item_dto(i).model_dump() for i in value]
            if isinstance(value, list)
            else _item_dto(value).model_dump()
            for key, value in saved.items()
        },
    }
