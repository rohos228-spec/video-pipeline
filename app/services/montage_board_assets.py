"""Файловые операции панели монтажа: upload / delete / archive."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project
from app.services.plan_shot2 import (
    effective_shot_from_artifact,
    shot2_video_file_pattern,
)

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _archive_dir(project: Project, sub: str) -> Path:
    d = project.data_dir / "old" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def archive_file(path: Path, project: Project, sub: str) -> Path | None:
    """Перенос в old/… с retry — на Windows файл часто занят /api/files или Defender."""
    if not path.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = _archive_dir(project, sub) / f"{stamp}_{path.name}"
    last_err: BaseException | None = None
    for attempt in range(1, 6):
        try:
            if not path.is_file():
                return dest if dest.is_file() else None
            path.replace(dest)
            logger.info("montage: archived {} → {}", path.name, dest)
            return dest
        except OSError as exc:
            last_err = exc
            try:
                if path.is_file():
                    dest.write_bytes(path.read_bytes())
                    path.unlink(missing_ok=True)
                if not path.exists() and dest.is_file():
                    logger.info(
                        "montage: archived (copy+unlink) {} → {} (try {})",
                        path.name,
                        dest,
                        attempt,
                    )
                    return dest
            except OSError as exc2:
                last_err = exc2
            time.sleep(0.12 * attempt)
    raise RuntimeError(
        f"не удалось архивировать {path.name}: {last_err}"
    ) from last_err


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


def iter_scene_images(scenes: Path, frame_number: int, *, shot: int) -> list[Path]:
    """Все кадры shot на диске (png/jpg/webp), без путаницы shot_01 ↔ shot_02."""
    if not scenes.is_dir():
        return []
    found: list[Path] = []
    seen: set[Path] = set()
    for ext in _IMAGE_EXTS:
        if shot == 2:
            pattern = f"frame_{frame_number:03d}_s2_*{ext}"
            for p in scenes.glob(pattern):
                key = p.resolve()
                if key not in seen:
                    seen.add(key)
                    found.append(p)
        else:
            pattern = f"frame_{frame_number:03d}_*{ext}"
            for p in scenes.glob(pattern):
                if "_s2_" in p.name:
                    continue
                key = p.resolve()
                if key not in seen:
                    seen.add(key)
                    found.append(p)
    return found


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
    new_resolved = new_path.resolve()
    for p in iter_scene_images(scenes, frame_number, shot=shot):
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
            if effective_shot_from_artifact(art.meta, art.path or "") == shot:
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
    deleted = False
    for p in iter_scene_images(scenes, frame_number, shot=shot):
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
            if effective_shot_from_artifact(art.meta, art.path or "") != shot:
                continue
            # На случай если glob не нашёл (редкое имя / иной суффикс) — архив по path.
            if art.path:
                ap = Path(art.path)
                if ap.is_file():
                    try:
                        archive_file(ap, project, "scenes")
                    except RuntimeError:
                        logger.warning(
                            "montage delete: artifact path busy, unlink {}",
                            ap.name,
                        )
                        try:
                            ap.unlink(missing_ok=True)
                        except OSError:
                            pass
                    deleted = True
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
                if art.path:
                    ap = Path(art.path)
                    if ap.is_file():
                        try:
                            archive_file(ap, project, "videos")
                        except RuntimeError:
                            try:
                                ap.unlink(missing_ok=True)
                            except OSError:
                                pass
                        deleted = True
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
