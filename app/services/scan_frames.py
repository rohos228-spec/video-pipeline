"""Сервис: найти кадры, у которых в БД есть `image_prompt`, но НЕТ
сгенерированной картинки на диске.

Используется кнопкой «🔍 Добить недостающие» в подменю шага 7
«Картинки»: после нормальной генерации могли остаться кадры без
файла (failed, прерванный воркер, ручное удаление .png). Этот скан
их находит, чтобы повторно прогнать только их.

Источник истины — диск: `<project.data_dir>/scenes/frame_<NNN>_*.png`
(см. формат имени в `generate_images.py:551`).
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, FrameStatus, Project


def _disk_has_frame_image(scenes_dir: Path, frame_number: int) -> bool:
    """Есть ли на диске картинка для кадра. Формат имени —
    `frame_<NNN>_<uuid8>.png` (см. generate_images.py:551)."""
    if not scenes_dir.exists():
        return False
    pattern = f"frame_{frame_number:03d}_*.png"
    # glob возвращает generator; any() короткозамыкается на первом hit.
    return any(scenes_dir.glob(pattern))


async def scan_missing_frames(
    session: AsyncSession, project: Project
) -> list[int]:
    """Список номеров кадров, у которых:
      - есть `image_prompt` в БД (не пустой),
      - но НЕТ файла `frame_<NNN>_*.png` в `<data_dir>/scenes/`.

    Файлы на диске — источник истины. Если файл удалили вручную, но
    в БД остался scene_image-artifact / `status=image_generated`,
    кадр всё равно попадёт в «недостающие». Это даёт юзеру способ
    перегенерить точечно.
    """
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    scenes_dir = project.data_dir / "scenes"
    missing: list[int] = []
    total_with_prompt = 0
    for fr in frames:
        if not (fr.image_prompt or "").strip():
            continue
        total_with_prompt += 1
        if not _disk_has_frame_image(scenes_dir, fr.number):
            missing.append(fr.number)
    logger.info(
        "[#{}] scan_missing_frames: всего кадров={}, с image_prompt={}, "
        "недостающих={}, scenes_dir={}",
        project.id, len(frames), total_with_prompt, len(missing), scenes_dir,
    )
    return missing


async def sync_frames_with_disk_images(
    session: AsyncSession, project: Project
) -> int:
    """Кадры с `frame_NNN_*.png` на диске → `image_generated`, чтобы
    воркер не перегенеривал их при «доделке недостающих»."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    scenes_dir = project.data_dir / "scenes"
    changed = 0
    for fr in frames:
        if fr.status in (
            FrameStatus.image_approved,
            FrameStatus.image_generated,
            FrameStatus.failed,
        ):
            continue
        if not _disk_has_frame_image(scenes_dir, fr.number):
            continue
        fr.status = FrameStatus.image_generated
        changed += 1
    if changed:
        await session.flush()
        logger.info(
            "[#{}] sync_frames_with_disk_images: {} кадров уже на диске → image_generated",
            project.id,
            changed,
        )
    return changed


async def reset_frames_to_image_prompt_ready(
    session: AsyncSession, project: Project, numbers: list[int]
) -> int:
    """Сбросить статус указанных кадров в `image_prompt_ready`, чтобы
    воркер подхватил их в `generating_images`. Возвращает кол-во
    реально изменённых кадров.

    Кадры без `image_prompt` пропускаются (нечего генерировать —
    они бы всё равно стали `failed` в `generate_images.run`).
    Также чистим `attrs['fail_reason']`, если он был выставлен
    предыдущим запуском.
    """
    if not numbers:
        return 0
    rows = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .where(Frame.number.in_(numbers))
        )
    ).scalars().all()
    changed = 0
    for fr in rows:
        if not (fr.image_prompt or "").strip():
            continue
        fr.status = FrameStatus.image_prompt_ready
        attrs = dict(fr.attrs or {})
        if "fail_reason" in attrs:
            del attrs["fail_reason"]
            fr.attrs = attrs
        changed += 1
    await session.flush()
    return changed


def _disk_has_frame_video(videos_dir: Path, frame_number: int) -> bool:
    """`clip_<NNN>_*.mp4` в data/.../videos/ (см. generate_videos.py)."""
    if not videos_dir.exists():
        return False
    return any(videos_dir.glob(f"clip_{frame_number:03d}_*.mp4"))


async def scan_missing_videos(
    session: AsyncSession, project: Project
) -> list[int]:
    """Кадры с animation_prompt и картинкой, но без clip_XXX_*.mp4 на диске."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    videos_dir = project.data_dir / "videos"
    scenes_dir = project.data_dir / "scenes"
    missing: list[int] = []
    for fr in frames:
        if not (fr.animation_prompt or "").strip():
            continue
        if not _disk_has_frame_image(scenes_dir, fr.number):
            continue
        if not _disk_has_frame_video(videos_dir, fr.number):
            missing.append(fr.number)
    logger.info(
        "[#{}] scan_missing_videos: кадров с anim_prompt+картинкой={}, "
        "без clip на диске={}, videos_dir={}",
        project.id,
        sum(1 for f in frames if (f.animation_prompt or "").strip()),
        len(missing),
        videos_dir,
    )
    return missing


async def reset_frames_for_video_regen(
    session: AsyncSession, project: Project, numbers: list[int]
) -> int:
    """Подготовить кадры к догенерации видео (generate_videos)."""
    if not numbers:
        return 0
    rows = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .where(Frame.number.in_(numbers))
        )
    ).scalars().all()
    changed = 0
    for fr in rows:
        if not (fr.animation_prompt or "").strip():
            continue
        if not _disk_has_frame_image(project.data_dir / "scenes", fr.number):
            continue
        if fr.status in (FrameStatus.video_approved, FrameStatus.done):
            continue
        fr.status = FrameStatus.animation_prompt_ready
        attrs = dict(fr.attrs or {})
        if "fail_reason" in attrs:
            del attrs["fail_reason"]
            fr.attrs = attrs
        changed += 1
    await session.flush()
    return changed
