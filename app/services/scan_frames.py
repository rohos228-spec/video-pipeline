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

from app.generation_options import is_skippable_empty_prompt
from app.models import Frame, FrameStatus, Project
from app.services.plan_shot2 import (
    SHOT2_PROMPT_ATTR,
    SHOT2_STATUS_ATTR,
    SHOT2_VIDEO_PROMPT_ATTR,
    MIN_SHOT2_VIDEO_PROMPT_LEN,
    disk_has_shot2_video,
    find_shot2_image,
    read_shot2_columns,
)

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


def disk_has_valid_shot2_image(scenes_dir: Path, frame_number: int) -> bool:
    path = find_shot2_image(scenes_dir, frame_number)
    return path is not None and is_valid_scene_image(path)


def frame_needs_shot1_image(fr: Frame, scenes_dir: Path) -> bool:
    """Кадр должен пройти outsee: есть промт, нет валидного PNG на диске."""
    if is_skippable_empty_prompt(fr.image_prompt or ""):
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
        if is_skippable_empty_prompt(fr.image_prompt or ""):
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


async def scan_missing_shot2_frames(
    session: AsyncSession, project: Project
) -> list[int]:
    """Кадры с промтом shot_02 в xlsx, но без ``frame_NNN_s2_*.png`` на диске."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    xlsx_path = project.data_dir / "project.xlsx"
    by_num = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
    scenes_dir = project.data_dir / "scenes"
    missing: list[int] = []
    expected = 0
    for fr in frames:
        info = by_num.get(fr.number)
        if info is None or not info.has_shot2:
            continue
        if not disk_has_valid_frame_image(scenes_dir, fr.number):
            continue
        expected += 1
        if not disk_has_valid_shot2_image(scenes_dir, fr.number):
            missing.append(fr.number)
    logger.info(
        "[#{}] scan_missing_shot2_frames: shot_02 ожидается={}, "
        "без PNG на диске={}, scenes_dir={}",
        project.id,
        expected,
        len(missing),
        scenes_dir,
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
        if is_skippable_empty_prompt(fr.image_prompt or ""):
            continue
        fr.status = FrameStatus.image_prompt_ready
        attrs = dict(fr.attrs or {})
        if "fail_reason" in attrs:
            del attrs["fail_reason"]
            fr.attrs = attrs
        changed += 1
    await session.flush()
    return changed


async def reset_shot2_to_prompt_ready(
    session: AsyncSession, project: Project, numbers: list[int]
) -> int:
    """Поставить shot_02 в очередь: ``shot2_status=image_prompt_ready``."""
    if not numbers:
        return 0
    xlsx_path = project.data_dir / "project.xlsx"
    by_num = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
    rows = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .where(Frame.number.in_(numbers))
        )
    ).scalars().all()
    scenes_dir = project.data_dir / "scenes"
    changed = 0
    for fr in rows:
        info = by_num.get(fr.number)
        if info is None or not info.has_shot2:
            continue
        if not disk_has_valid_frame_image(scenes_dir, fr.number):
            continue
        attrs = dict(fr.attrs or {})
        attrs[SHOT2_PROMPT_ATTR] = info.prompt
        attrs[SHOT2_STATUS_ATTR] = "image_prompt_ready"
        fr.attrs = attrs
        changed += 1
    await session.flush()
    return changed


def _disk_has_frame_video_shot1(videos_dir: Path, frame_number: int) -> bool:
    """``clip_<NNN>_*.mp4`` без ``_s2_`` в имени."""
    if not videos_dir.exists():
        return False
    return any(
        p
        for p in videos_dir.glob(f"clip_{frame_number:03d}_*.mp4")
        if "_s2_" not in p.name
    )


def _shot2_video_prompt(project: Project, fr: Frame) -> str:
    from app.services.animation_prompt_gpt import animation_prompt_shot2_in_plan_xlsx

    prompt = animation_prompt_shot2_in_plan_xlsx(project, fr.number)
    if len(prompt) >= MIN_SHOT2_VIDEO_PROMPT_LEN:
        return prompt
    attrs = fr.attrs or {}
    return (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()


async def scan_missing_videos_shot1(
    session: AsyncSession, project: Project
) -> list[int]:
    """Кадры с animation_prompt и PNG shot_01, но без clip_<NNN>_*.mp4 (не s2)."""
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
        if not disk_has_valid_frame_image(scenes_dir, fr.number):
            continue
        if not _disk_has_frame_video_shot1(videos_dir, fr.number):
            missing.append(fr.number)
    logger.info(
        "[#{}] scan_missing_videos_shot1: без clip shot_01={}, videos_dir={}",
        project.id,
        len(missing),
        videos_dir,
    )
    return missing


async def scan_missing_shot2_videos(
    session: AsyncSession, project: Project
) -> list[int]:
    """shot_02: PNG + промт R64 + clip shot_01 есть, ``clip_*_s2_*.mp4`` нет."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    xlsx_path = project.data_dir / "project.xlsx"
    by_num = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
    videos_dir = project.data_dir / "videos"
    scenes_dir = project.data_dir / "scenes"
    missing: list[int] = []
    expected = 0
    for fr in frames:
        info = by_num.get(fr.number)
        if info is None or not info.has_shot2:
            continue
        if not disk_has_valid_shot2_image(scenes_dir, fr.number):
            continue
        if len(_shot2_video_prompt(project, fr)) < MIN_SHOT2_VIDEO_PROMPT_LEN:
            continue
        expected += 1
        if not _disk_has_frame_video_shot1(videos_dir, fr.number):
            continue
        if not disk_has_shot2_video(videos_dir, fr.number):
            missing.append(fr.number)
    logger.info(
        "[#{}] scan_missing_shot2_videos: shot_02 ожидается={}, "
        "без clip s2={}, videos_dir={}",
        project.id,
        expected,
        len(missing),
        videos_dir,
    )
    return missing


async def scan_missing_videos(
    session: AsyncSession, project: Project
) -> list[int]:
    """Объединённый список кадров без clip shot_01 и/или shot_02."""
    s1 = await scan_missing_videos_shot1(session, project)
    s2 = await scan_missing_shot2_videos(session, project)
    return sorted(set(s1) | set(s2))


async def reset_shot2_for_video_regen(
    session: AsyncSession, project: Project, numbers: list[int]
) -> int:
    """Пометить shot_02 видео для догонки (generate_videos фаза shot_02)."""
    if not numbers:
        return 0
    from app.services.plan_shot2 import SHOT2_VIDEO_STATUS_ATTR

    xlsx_path = project.data_dir / "project.xlsx"
    by_num = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
    rows = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .where(Frame.number.in_(numbers))
        )
    ).scalars().all()
    videos_dir = project.data_dir / "videos"
    scenes_dir = project.data_dir / "scenes"
    changed = 0
    for fr in rows:
        info = by_num.get(fr.number)
        if info is None or not info.has_shot2:
            continue
        if not disk_has_valid_shot2_image(scenes_dir, fr.number):
            continue
        if len(_shot2_video_prompt(project, fr)) < MIN_SHOT2_VIDEO_PROMPT_LEN:
            continue
        if not _disk_has_frame_video_shot1(videos_dir, fr.number):
            continue
        attrs = dict(fr.attrs or {})
        attrs[SHOT2_VIDEO_STATUS_ATTR] = "video_prompt_ready"
        fr.attrs = attrs
        changed += 1
    await session.flush()
    return changed


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
        if not disk_has_valid_frame_image(project.data_dir / "scenes", fr.number):
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
