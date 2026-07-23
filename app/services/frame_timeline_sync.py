"""Синхронизация Frame.start_ts/end_ts с voice_full + xlsx + Whisper."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project
from app.services.frame_audio import (
    _voiceover_cells_for_frames,
    align_existing_voice_full,
    find_voice_full_on_disk,
    frame_clips_from_whisper,
)
from app.services.media_probe import probe_duration
from app.services.whisper import WordTS, load_words_json, whisper_words_fresh_for_audio
from app.settings import settings
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells

_PLACEHOLDER_VO_RE = re.compile(r"^кадр\s+\d+\.?$", re.IGNORECASE)


def is_placeholder_voiceover(text: str) -> bool:
    return bool(_PLACEHOLDER_VO_RE.match((text or "").strip()))


def timeline_frames_and_cells(
    project: Project,
    frames: list[Frame],
) -> tuple[list[Frame], list[tuple[int, str]]]:
    """Кадры и ячейки R49 для таймлайна — без заглушек «Кадр N» с диска."""
    numbers = [fr.number for fr in frames]
    raw_cells = read_plan_voiceover_cells(project, numbers)
    cells = _voiceover_cells_for_frames(project, frames, raw_cells)
    filtered_cells: list[tuple[int, str]] = []
    allowed: set[int] = set()
    for frame_number, text in cells:
        t = (text or "").strip()
        if not t or is_placeholder_voiceover(t):
            continue
        filtered_cells.append((frame_number, t))
        allowed.add(frame_number)
    timeline_frames = [fr for fr in frames if fr.number in allowed]
    return timeline_frames, filtered_cells


def frames_missing_timestamps(frames: list[Frame]) -> list[int]:
    missing: list[int] = []
    for fr in frames:
        if fr.start_ts is None or fr.end_ts is None:
            missing.append(fr.number)
    return missing


async def _latest_whisper_artifact(
    session: AsyncSession,
    project_id: int,
) -> Artifact | None:
    return (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _apply_clips_to_frames(
    frames: list[Frame],
    clips: list,
) -> list[int]:
    by_num = {c.frame_number: c for c in clips}
    updated: list[int] = []
    for fr in frames:
        clip = by_num.get(fr.number)
        if clip is None:
            continue
        fr.start_ts = clip.start_ts
        fr.end_ts = clip.end_ts
        fr.duration_seconds = clip.duration
        updated.append(fr.number)
    return updated


async def sync_frame_timestamps_from_voice(
    session: AsyncSession,
    project: Project,
    frames: list[Frame] | None = None,
    *,
    force_whisper: bool = False,
) -> dict[str, Any]:
    """Записать start_ts/end_ts в Frame по voice_full + текст R49 + Whisper."""
    if frames is None:
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number.asc())
                )
            ).scalars().all()
        )
    timeline_frames, cells = timeline_frames_and_cells(project, frames)
    if not timeline_frames:
        return {"skipped": "no frames with voiceover text (R49/xlsx)"}
    if not any(t.strip() for _, t in cells):
        return {"skipped": "empty voiceover cells"}

    voice_path = find_voice_full_on_disk(
        project.data_dir,
        meta=project.meta if isinstance(project.meta, dict) else None,
    )
    if voice_path is None or not voice_path.is_file():
        return {"skipped": "voice_full not on disk"}

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    master = await probe_duration(voice_path)

    if force_whisper:
        clips, _, _words = await align_existing_voice_full(
            project,
            timeline_frames,
            cells,
            voice_path,
            audio_dir,
            whisper_model=settings.whisper_model,
        )
        source = "whisper_realigned"
    else:
        whisper_art = await _latest_whisper_artifact(session, project.id)
        if whisper_art and whisper_art.path and whisper_words_fresh_for_audio(
            whisper_art, voice_path
        ):
            words = load_words_json(Path(whisper_art.path))
            clips = frame_clips_from_whisper(cells, words, master, voice_path)
            source = "words_json"
        else:
            clips, _, _words = await align_existing_voice_full(
                project,
                timeline_frames,
                cells,
                voice_path,
                audio_dir,
                whisper_model=settings.whisper_model,
            )
            source = "whisper_realigned"

    updated = _apply_clips_to_frames(timeline_frames, clips)
    if updated:
        await session.flush()
        logger.info(
            "[#{}] frame_timeline_sync: {} кадров ← {} ({} clips)",
            project.id,
            len(updated),
            source,
            len(clips),
        )
    return {
        "source": source,
        "updated": updated,
        "clip_count": len(clips),
        "voice_seconds": round(master, 2),
    }


async def sync_frame_timestamps_if_needed(
    session: AsyncSession,
    project: Project,
    frames: list[Frame] | None = None,
) -> dict[str, Any]:
    """Если у кадров с R49 нет start_ts — пересчитать из whisper/xlsx."""
    if frames is None:
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number.asc())
                )
            ).scalars().all()
        )
    timeline_frames, _cells = timeline_frames_and_cells(project, frames)
    if not timeline_frames:
        return {"skipped": "no timeline frames"}
    missing = frames_missing_timestamps(timeline_frames)
    if not missing:
        return {"skipped": "timestamps ok"}
    logger.info(
        "[#{}] frame_timeline_sync: нет таймкодов у кадров {} — пересчёт",
        project.id,
        missing[:20] if len(missing) > 20 else missing,
    )
    return await sync_frame_timestamps_from_voice(session, project, frames)
