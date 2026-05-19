"""Сервис: ресинхронизация БД с файлами на диске.

Сканирует папки проекта (`scenes/`, `videos/`) на предмет файлов, для
которых в БД нет соответствующего `Artifact`-а, и регистрирует их.
Также сбрасывает кадры с `FrameStatus.failed` (которые упали из-за
"scene_image file missing on disk"), если новая картинка нашлась.

Используется кнопкой "🔄 Перечитать xlsx" в меню проекта (одновременно
с импортом xlsx) — чтобы любой ручной/внешний рассинхрон БД↔диск
автоматически чинился.

Формат имён, который понимаем (тот же, что использует пайплайн):
  - картинки сцен: `frame_<NNN>_<любой_суффикс>.png` в `<data_dir>/scenes/`
  - видео-клипы:   `clip_<NNN>_<любой_суффикс>.mp4`  в `<data_dir>/videos/`

Не удаляем стейл-артефакты с несуществующими файлами — оставляем,
бот сам отфильтрует их по `Path(a.path).is_file()` при выборе самого
свежего "живого" артефакта (см. `generate_videos.py`).
"""
from __future__ import annotations

import re
import uuid as uuid_mod
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, FrameStatus, Project


_FRAME_PNG_RE = re.compile(
    r"^frame_(\d+)(?:_[A-Za-z0-9._-]+)?\.png$", re.IGNORECASE
)
_CLIP_MP4_RE = re.compile(
    r"^clip_(\d+)(?:_[A-Za-z0-9._-]+)?\.mp4$", re.IGNORECASE
)


def _norm_path(p: str | Path) -> str:
    """Нормализует путь для сравнения: абсолютный путь без зависимости
    от платформы-разделителей. SQLite хранит пути с `\\` на винде и `/`
    на линуксе — `Path.resolve()` приводит к локальному виду."""
    try:
        return str(Path(p).resolve())
    except Exception:
        # Не упасть на битых путях — просто строковое сравнение.
        return str(p)


async def rescan_project_disk(
    session: AsyncSession, project: Project,
) -> dict:
    """Сканирует папки проекта и регистрирует артефакты для найденных
    файлов, если таких ещё нет в БД.

    Returns:
        dict с ключами:
          - scene_images_added: список номеров кадров, для которых
            добавили scene_image
          - scene_videos_added: список номеров кадров, для которых
            добавили scene_video
          - frames_unfailed: список номеров кадров, которые сняли с
            FrameStatus.failed (потому что нашли новую картинку)
          - skipped_no_frame: список (kind, filename) — файлы с
            номером, для которого нет Frame в БД
    """
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    frame_by_n: dict[int, Frame] = {fr.number: fr for fr in frames}

    existing_arts = (
        await session.execute(
            select(Artifact).where(Artifact.project_id == project.id)
        )
    ).scalars().all()
    existing_paths: set[str] = {_norm_path(a.path) for a in existing_arts}

    scene_images_added: list[int] = []
    scene_videos_added: list[int] = []
    frames_unfailed: list[int] = []
    skipped_no_frame: list[tuple[str, str]] = []

    def _scan(
        dirname: str,
        regex: re.Pattern,
        kind: ArtifactKind,
        added_list: list[int],
    ) -> None:
        d = project.data_dir / dirname
        if not d.is_dir():
            logger.info(
                "rescan_disk: [#{}] папка не существует, пропускаю: {}",
                project.id, d,
            )
            return
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            m = regex.match(f.name)
            if not m:
                continue
            if _norm_path(f) in existing_paths:
                continue
            n = int(m.group(1))
            fr = frame_by_n.get(n)
            if fr is None:
                skipped_no_frame.append((kind.value, f.name))
                logger.warning(
                    "rescan_disk: [#{}] файл {} -> номер {}, но Frame "
                    "с таким номером нет в БД — пропускаю",
                    project.id, f.name, n,
                )
                continue
            session.add(Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=kind,
                uuid=uuid_mod.uuid4().hex,
                path=str(f),
            ))
            added_list.append(n)
            logger.info(
                "rescan_disk: [#{}] frame {} {} зарегистрирован: {}",
                project.id, n, kind.value, f.name,
            )

    _scan("scenes", _FRAME_PNG_RE, ArtifactKind.scene_image,
          scene_images_added)
    _scan("videos", _CLIP_MP4_RE, ArtifactKind.scene_video,
          scene_videos_added)

    # Если для кадра в статусе `failed` нашли новую картинку — снимаем
    # failed обратно в image_generated (или image_approved, если был
    # approved до фейла — но в общем случае возвращаем в
    # image_generated, чтобы пользователь подтвердил/перегенерил
    # видео). Без этого кадр останется failed и пайплайн его снова
    # пропустит на шаге видео.
    failed_frames_with_new_image = (
        await session.execute(
            select(Frame).where(
                Frame.project_id == project.id,
                Frame.number.in_(scene_images_added),
                Frame.status == FrameStatus.failed,
            )
        )
    ).scalars().all()
    for fr in failed_frames_with_new_image:
        fr.status = FrameStatus.image_generated
        frames_unfailed.append(fr.number)
        logger.info(
            "rescan_disk: [#{}] frame {} status: failed -> image_generated "
            "(нашли свежий scene_image на диске)",
            project.id, fr.number,
        )

    await session.flush()

    return {
        "scene_images_added": sorted(scene_images_added),
        "scene_videos_added": sorted(scene_videos_added),
        "frames_unfailed": sorted(frames_unfailed),
        "skipped_no_frame": skipped_no_frame,
    }
