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
from app.services.plan_shot2 import find_shot1_image, find_shot2_image, shot2_file_pattern, shot2_video_file_pattern


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
            meta_shot = (art.meta or {}).get("shot", 1)
            path_shot = 2 if art.path and "_s2_" in art.path else 1
            effective = meta_shot if meta_shot in (1, 2) else path_shot
            if effective == shot:
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
    existing = find_shot2_image(scenes, frame_number) if shot == 2 else find_shot1_image(scenes, frame_number)
    if existing is not None:
        archive_file(existing, project, "scenes")
    short = uuid.uuid4().hex[:8]
    if shot == 2:
        name = f"frame_{frame_number:03d}_s2_{short}{suffix}"
    else:
        name = f"frame_{frame_number:03d}_{short}{suffix}"
    dest = scenes / name
    dest.write_bytes(content)
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        session.add(
            Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_image,
                uuid=uuid.uuid4().hex,
                path=str(dest),
                meta={"shot": shot},
            )
        )
    await session.flush()
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
    if shot == 2:
        old = list(videos.glob(shot2_video_file_pattern(frame_number)))
    else:
        old = [p for p in videos.glob(f"clip_{frame_number:03d}_*.mp4") if "_s2_" not in p.name]
    for p in old:
        archive_file(p, project, "videos")
    short = uuid.uuid4().hex[:8]
    if shot == 2:
        name = f"clip_{frame_number:03d}_s2_{short}{suffix}"
    else:
        name = f"clip_{frame_number:03d}_{short}{suffix}"
    dest = videos / name
    dest.write_bytes(content)
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        session.add(
            Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid=uuid.uuid4().hex,
                path=str(dest),
                meta={"shot": shot},
            )
        )
    await session.flush()
    return dest
