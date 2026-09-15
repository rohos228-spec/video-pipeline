"""Создать Frame в БД по файлам scenes/ и videos/ (ручной перенос папок)."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import func, select
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


def quarantine_frame_media(project: Project, number: int) -> list[str]:
    """Убрать PNG/MP4 удалённого кадра, чтобы монтаж не воскресил его с диска."""
    data_dir = Path(project.data_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = data_dir / "old" / "scenes" / f"deleted-{stamp}_frame_{int(number):03d}"
    moved: list[str] = []
    for folder, pattern in (
        (data_dir / "scenes", f"frame_{int(number):03d}_*"),
        (data_dir / "videos", f"clip_{int(number):03d}_*"),
    ):
        if not folder.is_dir():
            continue
        hits = [p for p in folder.glob(pattern) if p.is_file()]
        if not hits:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for src in hits:
            target = dest / src.name
            if target.exists():
                target = dest / f"{src.stem}_{stamp}{src.suffix}"
            shutil.move(str(src), str(target))
            moved.append(src.name)
    if moved:
        logger.info(
            "[#{}] quarantine_frame_media #{} → {} ({})",
            project.id,
            number,
            dest.name,
            len(moved),
        )
    return moved


async def ensure_frames_from_disk_media(
    session: AsyncSession,
    project: Project,
    *,
    fill_gaps: bool = False,
) -> list[int]:
    """Создать Frame по PNG/MP4 на диске.

    По умолчанию только в пустом проекте. Иначе дырка после удаления
    кадра (остался ``frame_NNN_*.png``) снова становится карточкой — дубль.
    ``fill_gaps=True`` — явный импорт недостающих номеров.
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
    if existing and not fill_gaps:
        return []
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


async def _count_project_frames(session: AsyncSession, project_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(Frame.id)).where(Frame.project_id == project_id)
            )
        ).scalar_one()
        or 0
    )


async def bootstrap_project_frames_from_disk(
    session: AsyncSession,
    project: Project,
    *,
    sync_xlsx: bool = True,
) -> dict[str, Any]:
    """Подтянуть Frame в БД из project.xlsx и/или scenes/videos на диске.

    Идемпотентно: повторный вызов без изменений возвращает пустой summary.
    """
    summary: dict[str, Any] = {}
    frame_count = await _count_project_frames(session, project.id)

    xlsx = project.data_dir / "project.xlsx"
    if sync_xlsx and xlsx.is_file() and frame_count == 0:
        try:
            from app.services.chatgpt_xlsx import sync_project_xlsx

            info = await sync_project_xlsx(
                session,
                project,
                xlsx,
                keep_fields=True,
                update_frames_voiceover=True,
            )
            summary["xlsx"] = info
            frame_count = await _count_project_frames(session, project.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[#{}] bootstrap_project_frames_from_disk: xlsx failed: {}",
                project.id,
                exc,
            )
            summary["xlsx_error"] = str(exc)

    disk_numbers = discover_frame_numbers_on_disk(project.data_dir)
    if disk_numbers:
        summary["disk_frame_numbers"] = sorted(disk_numbers)

    created = await ensure_frames_from_disk_media(session, project)
    if created:
        summary["frames_created"] = created

    return summary
