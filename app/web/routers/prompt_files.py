"""REST: файлы промтов на диске (`prompts/<step>/*.md`).

Эндпоинты позволяют Node Studio в вебе показывать содержимое папки в
real-time: список вариантов, чтение/запись/удаление, скачивание и
загрузка .md-файлов. Имя файла санитизируется через
`prompt_library.is_valid_prompt_name` — path traversal невозможен.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.services.prompt_library import (
    DEFAULT_NAME,
    STEP_FOLDERS,
    delete_prompt,
    is_valid_prompt_name,
    list_prompts,
    prompt_path,
    read_prompt,
    step_dir,
    write_prompt,
)

router = APIRouter(prefix="/prompt-files", tags=["prompt-files"])


class PromptFileInfo(BaseModel):
    name: str
    filename: str
    size: int
    modified: float
    is_default: bool


class PromptFileContent(BaseModel):
    name: str
    filename: str
    content: str
    size: int
    modified: float


class PromptFileSavePayload(BaseModel):
    content: str


def _ensure_step(step_code: str) -> None:
    if step_code not in STEP_FOLDERS:
        raise HTTPException(
            status_code=404,
            detail=f"step '{step_code}' has no prompt folder",
        )


def _ensure_name(name: str) -> None:
    if not is_valid_prompt_name(name):
        raise HTTPException(status_code=400, detail=f"invalid prompt name: {name!r}")


@router.get("/{step_code}", response_model=list[PromptFileInfo])
async def list_prompt_files(step_code: str) -> list[PromptFileInfo]:
    """Список .md-файлов в `prompts/<step>/`."""
    _ensure_step(step_code)
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
                modified=stat.st_mtime,
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
        modified=stat.st_mtime,
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
    step_code: str, name: str, payload: PromptFileSavePayload
) -> PromptFileContent:
    _ensure_step(step_code)
    _ensure_name(name)
    write_prompt(step_code, name, payload.content)
    p = prompt_path(step_code, name)
    stat = p.stat()
    return PromptFileContent(
        name=name,
        filename=f"{name}.md",
        content=payload.content,
        size=stat.st_size,
        modified=stat.st_mtime,
    )


@router.delete("/{step_code}/{name}")
async def delete_prompt_file(step_code: str, name: str) -> dict[str, bool]:
    _ensure_step(step_code)
    _ensure_name(name)
    if name == DEFAULT_NAME:
        raise HTTPException(status_code=400, detail="default удалять нельзя")
    try:
        removed = delete_prompt(step_code, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not removed:
        raise HTTPException(status_code=404, detail="prompt file not found")
    return {"removed": True}


@router.post("/{step_code}/upload", response_model=PromptFileInfo)
async def upload_prompt_file(
    step_code: str,
    file: UploadFile = File(...),
    name: str | None = None,
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
                "40 байт UTF-8"
            ),
        )
    blob = await file.read()
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="file must be utf-8 text") from e
    write_prompt(step_code, raw_name, text)
    p = prompt_path(step_code, raw_name)
    stat = p.stat()
    return PromptFileInfo(
        name=raw_name,
        filename=f"{raw_name}.md",
        size=stat.st_size,
        modified=stat.st_mtime,
        is_default=(raw_name == DEFAULT_NAME),
    )
