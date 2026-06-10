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

# Реальные outsee 2K PNG обычно 300 KB–5 MB; thumb/preview ~50–100 KB.
_MIN_SCENE_IMAGE_BYTES = 200_000
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_RIFF_MAGIC = b"RIFF"
_WEBP_TAG = b"WEBP"


def newest_frame_image_path(scenes_dir: Path, frame_number: int) -> Path | None:
    """Самый свежий PNG shot_01 для кадра (без ``_s2_`` в имени)."""
    if not scenes_dir.is_dir():
        return None
    candidates = [
        p
        for p in scenes_dir.glob(f"frame_{frame_number:03d}_*.png")
        if "_s2_" not in p.name
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def is_valid_scene_image(path: Path) -> bool:
    """Настоящая сцена: достаточный размер и magic PNG/JPEG/WebP."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < _MIN_SCENE_IMAGE_BYTES:
        return False
    try:
        with path.open("rb") as f:
            head = f.read(16)
    except OSError:
        return False
    is_png = head.startswith(_PNG_MAGIC)
    is_jpeg = head.startswith(_JPEG_MAGIC)
    is_webp = head[:4] == _RIFF_MAGIC and head[8:12] == _WEBP_TAG
    return is_png or is_jpeg or is_webp


def disk_has_valid_frame_image(scenes_dir: Path, frame_number: int) -> bool:
    path = newest_frame_image_path(scenes_dir, frame_number)
    return path is not None and is_valid_scene_image(path)


def frame_needs_shot1_image(fr: Frame, scenes_dir: Path) -> bool:
    """Кадр должен пройти outsee: есть промт, нет валидного PNG на диске."""
    if not (fr.image_prompt or "").strip():
        return False
    if fr.status is FrameStatus.image_approved:
        return False
    if fr.status is FrameStatus.failed:
        return False
    return not disk_has_valid_frame_image(scenes_dir, fr.number)


def _disk_has_frame_image(scenes_dir: Path, frame_number: int) -> bool:
    """Есть ли на диске картинка для кадра. Формат имени —
    `frame_<NNN>_<uuid8>.png` (см. generate_images.py:551)."""
    return newest_frame_image_path(scenes_dir, frame_number) is not None


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
        if not disk_has_valid_frame_image(scenes_dir, fr.number):
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
        if not disk_has_valid_frame_image(scenes_dir, fr.number):
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
