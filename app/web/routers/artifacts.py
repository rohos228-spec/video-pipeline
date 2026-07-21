"""REST: /api/artifacts — список + бинарная отдача.

Также /api/files (вне /artifacts) — служит для отдачи media по абсолютному
пути из HITL.payload (photo_path/video_path), полезно когда в БД ещё нет
Artifact. Безопасно: разрешает только пути под `settings.data_dir`.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact
from app.settings import settings
from app.web.deps import get_session
from app.web.schemas import ArtifactDTO

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
files_router = APIRouter(tags=["files"])


@files_router.get("/files")
async def serve_data_file(path: str = Query(..., description="Абсолютный путь под data_dir")) -> FileResponse:
    """Отдаёт файл из data_dir (или его подкаталогов). Безопасный whitelist
    предотвращает path traversal — допускаем только пути, чей resolve
    начинается с data_dir.resolve().
    """
    candidate = Path(path).resolve()
    base = Path(settings.data_dir).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path outside data_dir") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    mime, _ = mimetypes.guess_type(str(candidate))
    return FileResponse(
        candidate,
        media_type=mime or "application/octet-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@router.get("", response_model=list[ArtifactDTO])
async def list_artifacts(
    project_id: int | None = None,
    frame_id: int | None = None,
    kind: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Artifact]:
    q = select(Artifact)
    if project_id is not None:
        q = q.where(Artifact.project_id == project_id)
    if frame_id is not None:
        q = q.where(Artifact.frame_id == frame_id)
    if kind is not None:
        q = q.where(Artifact.kind == kind)
    rows = (await session.execute(q.order_by(Artifact.id.desc()).limit(500))).scalars().all()
    return list(rows)


@router.get("/{artifact_uuid}", response_model=ArtifactDTO)
async def get_artifact(
    artifact_uuid: str, session: AsyncSession = Depends(get_session)
) -> Artifact:
    a = (
        await session.execute(select(Artifact).where(Artifact.uuid == artifact_uuid))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return a


@router.get("/{artifact_uuid}/file")
async def download_artifact(
    artifact_uuid: str, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    a = (
        await session.execute(select(Artifact).where(Artifact.uuid == artifact_uuid))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(a.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="file gone from disk")
    mime, _ = mimetypes.guess_type(str(path))
    return FileResponse(path, media_type=mime or "application/octet-stream")
