"""REST: /api/artifacts — список + бинарная отдача.

Также /api/files (вне /artifacts) — служит для отдачи media по абсолютному
пути из HITL.payload (photo_path/video_path), полезно когда в БД ещё нет
Artifact. Безопасно: разрешает только пути под `settings.data_dir`.
"""

from __future__ import annotations

import mimetypes
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact
from app.settings import settings
from app.web.deps import get_project_session, get_session
from app.web.schemas import ArtifactDTO

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
files_router = APIRouter(tags=["files"])


_PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".log", ".env", ".key", ".pem"}
_PRIVATE_NAMES = {"web_sessions", "sidebar_layout.json", ".env"}


def _is_private_file(p: Path) -> bool:
    name = p.name.lower()
    return p.suffix.lower() in _PRIVATE_SUFFIXES or name.startswith(".env") or name in _PRIVATE_NAMES         or any(name.startswith(n) for n in ("web_sessions",))


@files_router.get("/files")
async def serve_data_file(
    path: str = Query(..., description="Абсолютный путь под data_dir"),
    download: int = Query(0, description="1 = Content-Disposition: attachment"),
) -> FileResponse:
    """Отдаёт файл из data_dir (или его подкаталогов). Безопасный whitelist
    предотвращает path traversal — допускаем только пути, чей resolve
    начинается с data_dir.resolve().

    Имя на диске (.bin) не доверяем: magic → MIME и filename при download.
    """
    from app.services.gpt_api import suggested_name_and_mime

    candidate = Path(path).resolve()
    base = Path(settings.data_dir).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Путь выходит за пределы папки данных") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    if _is_private_file(candidate):
        raise HTTPException(status_code=403, detail="Служебный файл (база, лог, ключи) не отдаётся")
    download_name, mime = suggested_name_and_mime(candidate)
    kwargs: dict = {
        "media_type": mime or "application/octet-stream",
        "headers": {
            # v=mtime в URL — ключ кэша. immutable ломал превью монтажа:
            # после regen браузер держал старый PNG.
            "Cache-Control": "private, max-age=60, must-revalidate",
        },
    }
    if download:
        kwargs["filename"] = download_name
        # download — без долгого кэша в браузере как attachment
        kwargs["headers"] = {"Cache-Control": "private, no-store"}
    return FileResponse(candidate, **kwargs)


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
        raise HTTPException(status_code=404, detail="Артефакт не найден")
    return a


async def get_artifact_file_session(
    request: Request,
    project_id: Annotated[int | None, Query(gt=0)] = None,
) -> AsyncIterator[AsyncSession]:
    source = get_session() if project_id is None else get_project_session(request, project_id)
    async for session in source:
        yield session


@router.get("/{artifact_uuid}/file")
async def download_artifact(
    artifact_uuid: str,
    session: AsyncSession = Depends(get_artifact_file_session),
    project_id: Annotated[int | None, Query(gt=0)] = None,
) -> FileResponse:
    query = select(Artifact).where(Artifact.uuid == artifact_uuid)
    if project_id is not None:
        query = query.where(Artifact.project_id == project_id)
    a = (await session.execute(query)).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Артефакт не найден")
    path = Path(a.path)
    managed = "orchestrator" in (a.meta or {})
    if managed:
        from app.models import Project
        from app.services.artifact_verifier import contained_path, verify_artifact

        project = await session.get(Project, a.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Проект артефакта не найден")
        try:
            path = contained_path(path, project.data_dir)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Артефакт лежит вне папки проекта") from exc
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Файл пропал с диска")
    if managed:
        binding = a.meta.get("orchestrator")
        expected = binding.get("verification") if isinstance(binding, dict) else None
        if not isinstance(expected, dict) or expected.get("status") != "passed":
            raise HTTPException(status_code=409, detail="Проверка артефакта сейчас недоступна")
        actual = await verify_artifact(path, root=project.data_dir, kind=expected.get("kind"))
        if actual["status"] != "passed" or actual.get("sha256") != expected.get("sha256"):
            raise HTTPException(status_code=409, detail="Результат проверки артефакта изменился: обновите страницу и повторите")
    mime, _ = mimetypes.guess_type(str(path))
    return FileResponse(path, media_type=mime or "application/octet-stream",
                        headers={"Cache-Control": "private, no-store"} if managed else None)

@router.get("/project-cover/{project_id}")
async def project_cover(
    project_id: int, session: AsyncSession = Depends(get_project_session)
) -> FileResponse:
    """Обложка проекта для сайдбара: последний кадр, иначе референс персонажа."""
    from app.models import ArtifactKind

    for kinds in (
        [ArtifactKind.scene_image],
        [ArtifactKind.hero_reference, ArtifactKind.item_reference],
    ):
        rows = (
            await session.execute(
                select(Artifact)
                .where(Artifact.project_id == project_id, Artifact.kind.in_(kinds))
                .order_by(Artifact.id.desc())
                .limit(20)
            )
        ).scalars().all()
        for a in rows:
            if a.path and Path(a.path).is_file():
                mime, _ = mimetypes.guess_type(str(a.path))
                return FileResponse(
                    a.path,
                    media_type=mime or "image/png",
                    headers={"Cache-Control": "private, max-age=120"},
                )
    # нет обложки: 204 без тела — <img> уходит в onError-фолбэк без 404 в консоли
    from fastapi import Response

    return Response(status_code=204, headers={"Cache-Control": "private, max-age=120"})  # type: ignore[return-value]
