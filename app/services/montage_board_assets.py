"""Файловые операции панели монтажа: upload / delete / archive."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project
from app.services.plan_shot2 import (
    effective_shot_from_artifact,
    shot2_file_pattern,
    shot2_video_file_pattern,
)


def _archive_dir(project: Project, sub: str) -> Path:
    d = project.data_dir / "old" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def archive_file(path: Path, project: Project, sub: str) -> Path | None:
    if not path.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = _archive_dir(project, sub) / f"{stamp}_{path.name}"
    shutil.move(str(path), str(dest))
    logger.info("montage: archived {} → {}", path.name, dest)
    return dest


async def _frame(session: AsyncSession, project_id: int, frame_number: int) -> Frame | None:
    return (
        await session.execute(
            select(Frame).where(
                Frame.project_id == project_id,
                Frame.number == frame_number,
            )
        )
    ).scalar_one_or_none()


def _assert_new_file_ready(path: Path, *, min_bytes: int = 64) -> None:
    if not path.is_file():
        raise RuntimeError(f"новый файл не создан: {path.name}")
    if path.stat().st_size < min_bytes:
        raise RuntimeError(f"новый файл пустой или слишком мал: {path.name}")


async def finalize_scene_image(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    new_path: Path,
) -> None:
    """После успешной генерации/upload: архив старых файлов, artifact на new_path."""
    _assert_new_file_ready(new_path)
    scenes = project.data_dir / "scenes"
    if shot == 2:
        pattern = shot2_file_pattern(frame_number)
    else:
        pattern = f"frame_{frame_number:03d}_*.png"
    new_resolved = new_path.resolve()
    if scenes.is_dir():
        for p in list(scenes.glob(pattern)):
            if shot == 1 and "_s2_" in p.name:
                continue
            if p.resolve() == new_resolved:
                continue
            archive_file(p, project, "scenes")
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_image,
                )
            )
        ).scalars().all()
        for art in arts:
            meta_shot = (art.meta or {}).get("shot", 1)
            if (shot == 2 and meta_shot == 2) or (shot == 1 and meta_shot != 2):
                await session.delete(art)
        session.add(
            Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_image,
                uuid=uuid.uuid4().hex,
                path=str(new_path),
                meta={"shot": shot},
            )
        )
    await session.flush()


async def finalize_scene_video(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    new_path: Path,
) -> None:
    """После успешной генерации/upload: архив старых клипов, artifact на new_path."""
    _assert_new_file_ready(new_path, min_bytes=1024)
    videos = project.data_dir / "videos"
    if shot == 2:
        globs = [shot2_video_file_pattern(frame_number)]
    else:
        globs = [f"clip_{frame_number:03d}_*.mp4"]
    new_resolved = new_path.resolve()
    if videos.is_dir():
        for g in globs:
            for p in list(videos.glob(g)):
                if shot == 1 and "_s2_" in p.name:
                    continue
                if p.resolve() == new_resolved:
                    continue
                archive_file(p, project, "videos")
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalars().all()
        for art in arts:
            if effective_shot_from_artifact(art.meta, art.path or "") == shot:
                await session.delete(art)
        session.add(
            Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid=uuid.uuid4().hex,
                path=str(new_path),
                meta={"shot": shot},
            )
        )
    await session.flush()


async def delete_scene_image(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
) -> bool:
    scenes = project.data_dir / "scenes"
    if shot == 2:
        pattern = shot2_file_pattern(frame_number)
    else:
        pattern = f"frame_{frame_number:03d}_*.png"
    deleted = False
    if scenes.is_dir():
        for p in list(scenes.glob(pattern)):
            if shot == 1 and "_s2_" in p.name:
                continue
            archive_file(p, project, "scenes")
            deleted = True
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_image,
                )
            )
        ).scalars().all()
        for art in arts:
            meta_shot = (art.meta or {}).get("shot", 1)
            if (shot == 2 and meta_shot == 2) or (shot == 1 and meta_shot != 2):
                await session.delete(art)
                deleted = True
    await session.flush()
    return deleted


async def delete_scene_video(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
) -> bool:
    videos = project.data_dir / "videos"
    if shot == 2:
        globs = [shot2_video_file_pattern(frame_number)]
    else:
        globs = [f"clip_{frame_number:03d}_*.mp4"]
    deleted = False
    if videos.is_dir():
        for g in globs:
            for p in list(videos.glob(g)):
                if shot == 1 and "_s2_" in p.name:
                    continue
                archive_file(p, project, "videos")
                deleted = True
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalars().all()
        for art in arts:
            if effective_shot_from_artifact(art.meta, art.path or "") == shot:
                await session.delete(art)
                deleted = True
    await session.flush()
    return deleted


async def save_scene_image_upload(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    content: bytes,
    suffix: str,
) -> Path:
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    short = uuid.uuid4().hex[:8]
    if shot == 2:
        name = f"frame_{frame_number:03d}_s2_{short}{suffix}"
    else:
        name = f"frame_{frame_number:03d}_{short}{suffix}"
    dest = scenes / name
    dest.write_bytes(content)
    await finalize_scene_image(session, project, frame_number, shot=shot, new_path=dest)
    return dest


async def save_scene_video_upload(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    content: bytes,
    suffix: str,
) -> Path:
    videos = project.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    short = uuid.uuid4().hex[:8]
    if shot == 2:
        name = f"clip_{frame_number:03d}_s2_{short}{suffix}"
    else:
        name = f"clip_{frame_number:03d}_{short}{suffix}"
    dest = videos / name
    dest.write_bytes(content)
    await finalize_scene_video(session, project, frame_number, shot=shot, new_path=dest)
    return dest
