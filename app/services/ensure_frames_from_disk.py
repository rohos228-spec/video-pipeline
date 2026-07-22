"""Создать Frame в БД по файлам scenes/ и videos/ (ручной перенос папок)."""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, FrameStatus, Project

_MEDIA_FRAME_RE = re.compile(
    r"^(?:frame|clip)_(\d{3})(?:_s2)?_",
    re.IGNORECASE,
)


def discover_frame_numbers_on_disk(data_dir: Path) -> set[int]:
    """Номера кадров из ``scenes/frame_NNN_*`` и ``videos/clip_NNN_*``."""
    numbers: set[int] = set()
    scenes = data_dir / "scenes"
    videos = data_dir / "videos"
    globs: list[tuple[Path, str]] = []
    if scenes.is_dir():
        for pat in ("frame_*.png", "frame_*.jpg", "frame_*.jpeg", "frame_*.webp"):
            globs.append((scenes, pat))
    if videos.is_dir():
        globs.append((videos, "clip_*.mp4"))
    for folder, pat in globs:
        for path in folder.glob(pat):
            m = _MEDIA_FRAME_RE.match(path.name)
            if m:
                numbers.add(int(m.group(1)))
    return numbers


def _disk_has_video(videos_dir: Path, number: int) -> bool:
    if not videos_dir.is_dir():
        return False
    return any(videos_dir.glob(f"clip_{number:03d}_*.mp4"))


def _disk_has_image(scenes_dir: Path, number: int) -> bool:
    if not scenes_dir.is_dir():
        return False
    return any(
        p
        for p in scenes_dir.glob(f"frame_{number:03d}_*.*")
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )


async def ensure_frames_from_disk_media(
    session: AsyncSession,
    project: Project,
) -> list[int]:
    """Создать отсутствующие Frame по PNG/MP4 на диске.

    Нужно, когда пользователь скопировал ``scenes/`` и ``videos/`` в новый
    проект без ``project.xlsx`` / шага «Разбивка» — иначе монтаж пустой.
    """
    numbers = discover_frame_numbers_on_disk(project.data_dir)
    if not numbers:
        return []

    existing = {
        int(n)
        for n in (
            await session.execute(
                select(Frame.number).where(Frame.project_id == project.id)
            )
        ).scalars().all()
    }
    missing = sorted(n for n in numbers if n not in existing)
    if not missing:
        return []

    scenes_dir = project.data_dir / "scenes"
    videos_dir = project.data_dir / "videos"
    created: list[int] = []
    for n in missing:
        if _disk_has_video(videos_dir, n):
            status = FrameStatus.video_generated
        elif _disk_has_image(scenes_dir, n):
            status = FrameStatus.image_generated
        else:
            status = FrameStatus.planned
        session.add(
            Frame(
                project_id=project.id,
                number=n,
                voiceover_text=f"Кадр {n}",
                status=status,
                attrs={"from_disk_media": True},
            )
        )
        created.append(n)

    await session.flush()
    logger.info(
        "[#{}] ensure_frames_from_disk_media: создано {} кадров из scenes/videos {}",
        project.id,
        len(created),
        created[:20] if len(created) > 20 else created,
    )

    try:
        from app.services.artifact_recovery import (
            recover_scene_images_from_disk,
            recover_scene_videos_from_disk,
        )

        await recover_scene_images_from_disk(session, project)
        await recover_scene_videos_from_disk(session, project)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[#{}] ensure_frames_from_disk_media: artifact recover failed: {}",
            project.id,
            exc,
        )

    return created
