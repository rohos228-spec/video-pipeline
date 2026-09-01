"""API: Studio GPT workspace (история / вложения / ask / сохранение)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services import gpt_workspace as gw
from app.services.gpt_client import GptApiUnavailable
from app.web.deps import get_session as get_db_session

router = APIRouter(prefix="/gpt-workspace", tags=["gpt-workspace"])


class CreateSessionBody(BaseModel):
    title: str | None = None


class RenameBody(BaseModel):
    title: str = Field(..., min_length=1)


class AskBody(BaseModel):
    message: str = Field(..., min_length=1)
    with_attachments: bool = True


class SaveToProjectBody(BaseModel):
    project_id: int
    output_name: str
    as_name: str | None = None


class SaveVoiceoverBody(BaseModel):
    project_id: int
    message_id: str | None = None


@router.get("/sessions")
async def list_sessions() -> dict[str, Any]:
    return {"sessions": gw.list_sessions()}


@router.post("/sessions")
async def create_session(body: CreateSessionBody | None = None) -> dict[str, Any]:
    title = body.title if body else None
    return gw.create_session(title=title)


@router.get("/sessions/{session_id}")
async def get_workspace_session(session_id: str) -> dict[str, Any]:
    try:
        return gw.get_session(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/sessions/{session_id}")
async def rename_workspace_session(session_id: str, body: RenameBody) -> dict[str, Any]:
    try:
        return gw.rename_session(session_id, body.title)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/sessions/{session_id}")
async def delete_workspace_session(session_id: str) -> dict[str, Any]:
    gw.delete_session(session_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/attachments")
async def upload_attachment(
    session_id: str,
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
) -> dict[str, Any]:
    """Один файл (`file`) или пачка (`files`) — как batch attach в ChatGPT."""
    uploads: list[UploadFile] = []
    if files:
        uploads.extend(files)
    if file is not None:
        uploads.append(file)
    if not uploads:
        raise HTTPException(status_code=400, detail="нет файлов")
    saved: list[dict[str, Any]] = []
    try:
        for uf in uploads:
            data = await uf.read()
            if not data:
                raise HTTPException(
                    status_code=400,
                    detail=f"пустой файл: {uf.filename or '?'}",
                )
            saved.append(
                gw.save_attachment(session_id, uf.filename or "file.bin", data)
            )
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if len(saved) == 1:
        return saved[0]
    return {"files": saved, "count": len(saved)}


@router.delete("/sessions/{session_id}/attachments/{name}")
async def delete_attachment(session_id: str, name: str) -> dict[str, Any]:
    try:
        gw.delete_attachment(session_id, name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"ok": True}


@router.post("/sessions/{session_id}/attachments/{name}/to-outputs")
async def attachment_to_outputs(session_id: str, name: str) -> dict[str, Any]:
    """Скопировать вложение в Результаты (скачиваемый output)."""
    try:
        return gw.copy_attachment_to_outputs(session_id, name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/sessions/{session_id}/outputs.zip")
async def download_outputs_zip(session_id: str) -> FileResponse:
    """Скачать все Результаты одним zip."""
    try:
        path = gw.build_outputs_zip(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"gpt_outputs_{session_id}.zip",
    )


@router.post("/sessions/{session_id}/ask")
async def ask(session_id: str, body: AskBody) -> dict[str, Any]:
    try:
        return await gw.ask(
            session_id,
            body.message,
            with_attachments=body.with_attachments,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except GptApiUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)[:800]) from e


@router.post("/sessions/{session_id}/ask-stream")
async def ask_stream_endpoint(session_id: str, body: AskBody) -> StreamingResponse:
    try:
        gen = gw.ask_stream(
            session_id,
            body.message,
            with_attachments=body.with_attachments,
        )
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except GptApiUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)[:800]) from e


@router.post("/sessions/{session_id}/save-to-project")
async def save_to_project(
    session_id: str,
    body: SaveToProjectBody,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    project = await db.get(Project, body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        return gw.save_output_to_project(
            session_id,
            output_name=body.output_name,
            project_data_dir=project.data_dir,
            as_name=body.as_name,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sessions/{session_id}/save-voiceover")
async def save_voiceover(
    session_id: str,
    body: SaveVoiceoverBody,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    project = await db.get(Project, body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        return gw.save_reply_as_voiceover(
            session_id,
            project_data_dir=project.data_dir,
            message_id=body.message_id,
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
