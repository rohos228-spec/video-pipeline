"""REST: файлы промтов на диске (`prompts/<step>/*.md`).

Эндпоинты позволяют Node Studio в вебе показывать содержимое папки в
real-time: список вариантов, чтение/запись/удаление, скачивание и
загрузка .md-файлов. Имя файла санитизируется через
`prompt_library.is_valid_prompt_name` — path traversal невозможен.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import local_library as lib
from app.services.prompt_history import (
    bootstrap_saved_at_from_history,
    list_prompt_versions,
    read_prompt_version,
    rename_prompt_file,
    rename_prompt_version_label,
    restore_prompt_version,
    write_prompt_with_history,
)
from app.services.prompt_library import (
    DEFAULT_NAME,
    PROMPT_SOURCE_LABELS,
    STEP_FOLDERS,
    delete_prompt,
    get_prompt_saved_at,
    is_valid_prompt_name,
    list_prompts,
    prompt_path,
    read_prompt,
    resolve_project_prompt_with_source,
    step_dir,
)
from app.web.deps import get_session

router = APIRouter(prefix="/prompt-files", tags=["prompt-files"])


@router.get("/global-active")
async def get_global_active_variants() -> dict[str, str]:
    """Последние активные .md по шагам (общие для всех проектов)."""
    from app.services.prompt_active_global import load_global_active

    return load_global_active()


class PromptFileInfo(BaseModel):
    name: str
    filename: str
    size: int
    modified: float | None
    is_default: bool


class PromptFileContent(BaseModel):
    name: str
    filename: str
    content: str
    size: int
    modified: float | None


class PromptResolveInfo(BaseModel):
    name: str
    source: str
    source_label: str
    modified: float | None


class PromptFileSavePayload(BaseModel):
    content: str


class PromptVersionInfo(BaseModel):
    id: str
    label: str
    saved_at: float
    size: int


class PromptVersionContent(BaseModel):
    id: str
    label: str
    content: str
    saved_at: float
    size: int


class PromptRenamePayload(BaseModel):
    new_name: str


class PromptVersionLabelPayload(BaseModel):
    label: str


def _ensure_step(step_code: str) -> None:
    if step_code not in STEP_FOLDERS:
        raise HTTPException(
            status_code=404,
            detail=f"step '{step_code}' has no prompt folder",
        )


def _ensure_name(name: str) -> None:
    if not is_valid_prompt_name(name):
        raise HTTPException(status_code=400, detail=f"invalid prompt name: {name!r}")


def _library_prompt_path(step_code: str, name: str) -> str:
    return (Path("prompts") / STEP_FOLDERS[step_code] / f"{name}.md").as_posix()


def _prompt_modified(step_code: str, name: str, p: Path) -> float | None:
    return get_prompt_saved_at(step_code, name)


@router.get("/{step_code}/resolve", response_model=PromptResolveInfo)
async def resolve_prompt_for_project(
    step_code: str,
    project_id: int = Query(...),
    node_key: str | None = Query(None),
    slot_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> PromptResolveInfo:
    from app.models import Project

    _ensure_step(step_code)
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    overrides = project.prompt_overrides if isinstance(project.prompt_overrides, dict) else {}
    meta = project.meta if isinstance(project.meta, dict) else {}
    name, source = resolve_project_prompt_with_source(
        overrides,
        step_code,
        meta=meta,
        node_key=node_key,
        slot_id=slot_id,
    )
    p = prompt_path(step_code, name)
    return PromptResolveInfo(
        name=name,
        source=source,
        source_label=PROMPT_SOURCE_LABELS.get(source, source),
        modified=_prompt_modified(step_code, name, p) if p.exists() else None,
    )


@router.get("/{step_code}", response_model=list[PromptFileInfo])
async def list_prompt_files(step_code: str) -> list[PromptFileInfo]:
    """Список .md-файлов в `prompts/<step>/`."""
    _ensure_step(step_code)
    bootstrap_saved_at_from_history(step_code)
    folder = step_dir(step_code)
    out: list[PromptFileInfo] = []
    for name in list_prompts(step_code):
        p = folder / f"{name}.md"
        if not p.exists():
            continue
        stat = p.stat()
        out.append(
            PromptFileInfo(
                name=name,
                filename=f"{name}.md",
                size=stat.st_size,
                modified=_prompt_modified(step_code, name, p),
                is_default=(name == DEFAULT_NAME),
            )
        )
    return out


@router.get("/{step_code}/{name}/content", response_model=PromptFileContent)
async def get_prompt_file(step_code: str, name: str) -> PromptFileContent:
    _ensure_step(step_code)
    _ensure_name(name)
    p = prompt_path(step_code, name)
    if not p.exists():
        raise HTTPException(status_code=404, detail="prompt file not found")
    stat = p.stat()
    return PromptFileContent(
        name=name,
        filename=f"{name}.md",
        content=read_prompt(step_code, name),
        size=stat.st_size,
        modified=_prompt_modified(step_code, name, p),
    )


@router.get("/{step_code}/{name}/download")
async def download_prompt_file(step_code: str, name: str) -> FileResponse:
    _ensure_step(step_code)
    _ensure_name(name)
    p = prompt_path(step_code, name)
    if not p.exists():
        raise HTTPException(status_code=404, detail="prompt file not found")
    return FileResponse(
        path=str(p),
        filename=f"{name}.md",
        media_type="text/markdown; charset=utf-8",
    )


@router.put("/{step_code}/{name}", response_model=PromptFileContent)
async def save_prompt_file(
    step_code: str,
    name: str,
    payload: PromptFileSavePayload,
    session: AsyncSession = Depends(get_session),
) -> PromptFileContent:
    _ensure_step(step_code)
    _ensure_name(name)
    write_prompt_with_history(step_code, name, payload.content)
    file_path = _library_prompt_path(step_code, name)
    await lib.create_or_update_item(
        session,
        kind="prompt",
        key=file_path,
        title=name,
        file_path=file_path,
        content=payload.content,
        message=f"save prompt file {step_code}/{name}",
        author="studio",
        source="prompt_files",
        meta={"step_code": step_code, "name": name},
        force_version=True,
    )
    from app.services.prompts import sync_step_prompt_to_db

    await sync_step_prompt_to_db(session, step_code, payload.content)
    await session.commit()
    p = prompt_path(step_code, name)
    stat = p.stat()
    return PromptFileContent(
        name=name,
        filename=f"{name}.md",
        content=payload.content,
        size=stat.st_size,
        modified=_prompt_modified(step_code, name, p),
    )


@router.delete("/{step_code}/{name}")
async def delete_prompt_file(
    step_code: str,
    name: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    _ensure_step(step_code)
    _ensure_name(name)
    if name == DEFAULT_NAME:
        raise HTTPException(status_code=400, detail="default удалять нельзя")
    file_path = _library_prompt_path(step_code, name)
    item = await lib.get_item_by_key(session, "prompt", file_path)
    try:
        removed = delete_prompt(step_code, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not removed:
        raise HTTPException(status_code=404, detail="prompt file not found")
    await lib.log_event(
        session,
        "deleted",
        item=item,
        payload={"step_code": step_code, "name": name, "file_path": file_path},
    )
    await session.commit()
    return {"removed": True}


@router.get("/{step_code}/{name}/history", response_model=list[PromptVersionInfo])
async def list_prompt_file_history(step_code: str, name: str) -> list[PromptVersionInfo]:
    _ensure_step(step_code)
    _ensure_name(name)
    return [PromptVersionInfo(**row) for row in list_prompt_versions(step_code, name)]


@router.get("/{step_code}/{name}/history/{version_id}/content", response_model=PromptVersionContent)
async def get_prompt_file_history_content(
    step_code: str, name: str, version_id: str
) -> PromptVersionContent:
    _ensure_step(step_code)
    _ensure_name(name)
    try:
        content = read_prompt_version(step_code, name, version_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    meta = next((v for v in list_prompt_versions(step_code, name) if v["id"] == version_id), None)
    if meta is None:
        raise HTTPException(status_code=404, detail="version not found")
    return PromptVersionContent(
        id=version_id,
        label=str(meta["label"]),
        content=content,
        saved_at=float(meta["saved_at"]),
        size=len(content.encode("utf-8")),
    )


@router.patch("/{step_code}/{name}/history/{version_id}", response_model=PromptVersionInfo)
async def rename_prompt_file_history_label(
    step_code: str, name: str, version_id: str, payload: PromptVersionLabelPayload
) -> PromptVersionInfo:
    _ensure_step(step_code)
    _ensure_name(name)
    try:
        row = rename_prompt_version_label(step_code, name, version_id, payload.label.strip())
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return PromptVersionInfo(**row)


@router.post("/{step_code}/{name}/history/{version_id}/restore")
async def restore_prompt_file_history(
    step_code: str, name: str, version_id: str
) -> PromptFileContent:
    _ensure_step(step_code)
    _ensure_name(name)
    try:
        content = restore_prompt_version(step_code, name, version_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    p = prompt_path(step_code, name)
    stat = p.stat()
    return PromptFileContent(
        name=name,
        filename=f"{name}.md",
        content=content,
        size=stat.st_size,
        modified=_prompt_modified(step_code, name, p),
    )


@router.patch("/{step_code}/{name}/rename", response_model=PromptFileInfo)
async def rename_prompt_file_route(
    step_code: str, name: str, payload: PromptRenamePayload
) -> PromptFileInfo:
    _ensure_step(step_code)
    _ensure_name(name)
    new_name = payload.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="new_name required")
    try:
        final = rename_prompt_file(step_code, name, new_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    p = prompt_path(step_code, final)
    stat = p.stat()
    return PromptFileInfo(
        name=final,
        filename=f"{final}.md",
        size=stat.st_size,
        modified=_prompt_modified(step_code, final, p),
        is_default=(final == DEFAULT_NAME),
    )


@router.post("/{step_code}/upload", response_model=PromptFileInfo)
async def upload_prompt_file(
    step_code: str,
    file: UploadFile = File(...),
    name: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> PromptFileInfo:
    """Загрузить .md-файл в папку шага.

    Имя файла берётся из `name` (если задано) либо из `file.filename`
    (без расширения). Если файл с таким именем уже есть — перезаписываем.
    """
    _ensure_step(step_code)
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    raw_name = name or file.filename
    # Срезаем расширение .md если есть.
    if raw_name.lower().endswith(".md"):
        raw_name = raw_name[:-3]
    raw_name = raw_name.strip()
    if not raw_name:
        raise HTTPException(status_code=400, detail="empty prompt name")
    if not is_valid_prompt_name(raw_name):
        raise HTTPException(
            status_code=400,
            detail=(
                "имя промта содержит запрещённые символы или превышает "
                "255 байт UTF-8"
            ),
        )
    blob = await file.read()
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="file must be utf-8 text") from e
    write_prompt_with_history(step_code, raw_name, text)
    file_path = _library_prompt_path(step_code, raw_name)
    await lib.create_or_update_item(
        session,
        kind="prompt",
        key=file_path,
        title=raw_name,
        file_path=file_path,
        content=text,
        message=f"upload prompt file {step_code}/{raw_name}",
        author="studio",
        source="prompt_files_upload",
        meta={"step_code": step_code, "name": raw_name},
        force_version=True,
    )
    from app.services.prompts import sync_step_prompt_to_db

    await sync_step_prompt_to_db(session, step_code, text)
    await session.commit()
    p = prompt_path(step_code, raw_name)
    stat = p.stat()
    return PromptFileInfo(
        name=raw_name,
        filename=f"{raw_name}.md",
        size=stat.st_size,
        modified=_prompt_modified(step_code, raw_name, p),
        is_default=(raw_name == DEFAULT_NAME),
    )
