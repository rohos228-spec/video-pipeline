"""Восстановление записей Artifact из файлов на диске (после сбоя сессии / отката БД)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, FrameStatus, Project

_CLIP_RE = re.compile(r"^clip_(\d{3})_", re.I)
_FRAME_MP3_RE = re.compile(r"^frame_(\d{3})\.mp3$", re.I)


async def recover_scene_videos_from_disk(
    session: AsyncSession, project: Project
) -> list[int]:
    """Привязать clip_XXX_*.mp4 из data/.../videos/ к Frame как scene_video."""
    videos_dir = project.data_dir / "videos"
    if not videos_dir.is_dir():
        return []
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    by_number = {f.number: f for f in frames}
    recovered: list[int] = []
    for path in sorted(videos_dir.glob("clip_*.mp4")):
        m = _CLIP_RE.match(path.name)
        if not m:
            continue
        num = int(m.group(1))
        fr = by_number.get(num)
        if fr is None:
            continue
        existing = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
                .order_by(Artifact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None and Path(existing.path).is_file():
            if fr.status not in (
                FrameStatus.video_generated,
                FrameStatus.video_approved,
                FrameStatus.done,
            ):
                fr.status = FrameStatus.video_generated
            continue
        session.add(
            Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid=uuid.uuid4().hex,
                path=str(path.resolve()),
            )
        )
        if fr.status not in (
            FrameStatus.video_generated,
            FrameStatus.video_approved,
            FrameStatus.done,
        ):
            fr.status = FrameStatus.video_generated
        recovered.append(num)
    if recovered:
        await session.flush()
        logger.info(
            "[#{}] artifact_recovery: scene_video с диска для кадров {}",
            project.id,
            recovered,
        )
    return recovered


async def recover_audio_from_disk(
    session: AsyncSession, project: Project
) -> bool:
    """Зарегистрировать voice_full_*.mp3 как ArtifactKind.audio, если записи нет."""
    existing = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.audio,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None and Path(existing.path).is_file():
        return False

    audio_dir = project.data_dir / "audio"
    if not audio_dir.is_dir():
        return False

    candidates = sorted(
        audio_dir.glob("voice_full_*.mp3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        legacy = audio_dir / "voice_full.mp3"
        if legacy.is_file():
            candidates = [legacy]

    full_path = next((p for p in candidates if p.is_file()), None)
    if full_path is None:
        return False

    clip_meta: list[dict] = []
    for mp3 in sorted(audio_dir.glob("frame_*.mp3")):
        m = _FRAME_MP3_RE.match(mp3.name)
        if m:
            clip_meta.append({"frame_number": int(m.group(1)), "path": str(mp3)})

    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.audio,
            uuid=uuid.uuid4().hex,
            path=str(full_path.resolve()),
            meta={
                "mode": "per_frame",
                "recovered_from_disk": True,
                "clip_count": len(clip_meta),
                "clips": clip_meta,
            },
        )
    )
    await session.flush()
    logger.info(
        "[#{}] artifact_recovery: audio ← {} ({} frame mp3)",
        project.id,
        full_path.name,
        len(clip_meta),
    )
    return True


async def recover_whisper_from_disk(
    session: AsyncSession, project: Project
) -> bool:
    """Подхватить последний words_*.json в audio/, если артефакта нет."""
    existing = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None and Path(existing.path).is_file():
        return False

    audio_dir = project.data_dir / "audio"
    if not audio_dir.is_dir():
        return False
    candidates = sorted(
        audio_dir.glob("words_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return False
    path = candidates[0]
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.whisper_words,
            uuid=uuid.uuid4().hex,
            path=str(path.resolve()),
        )
    )
    await session.flush()
    logger.info("[#{}] artifact_recovery: whisper_words ← {}", project.id, path.name)
    return True


async def recover_before_assemble(session: AsyncSession, project: Project) -> None:
    await recover_scene_videos_from_disk(session, project)
    await recover_audio_from_disk(session, project)
    await recover_whisper_from_disk(session, project)
