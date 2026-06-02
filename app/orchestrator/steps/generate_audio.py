"""Шаг 10: озвучка plan R49 — вариант B (один mp3 на ячейку / кадр).

Каждая колонка листа «план» (C49, D49, …) → отдельный TTS → frame_NNN.mp3.
Длительность кадра = ffprobe(фрагмент). Склейка → voice_full.
Whisper — один проход по voice_full (точнее, чем 30 отдельных фрагментов).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.elevenlabs import ElevenLabsBot
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    Project,
    ProjectStatus,
)
from app.services.artifact_recovery import (
    recover_audio_from_disk,
    recover_scene_videos_from_disk,
)
from app.services.frame_audio import synthesize_per_frame_audio
from app.services.mapper import extract_local_frame_words
from app.services.media_probe import probe_duration
from app.services.whisper import dump_words_json, transcribe_words
from app.settings import settings
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_audio:
        return
    logger.info("[#{}] generate_audio starting (per-frame TTS, plan R49)", project.id)

    await recover_scene_videos_from_disk(session, project)
    if await recover_audio_from_disk(session, project):
        project.status = ProjectStatus.audio_ready
        await session.flush()
        logger.info(
            "[#{}] generate_audio: озвучка уже на диске — audio_ready",
            project.id,
        )
        return

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров")

    cells = read_plan_voiceover_cells(project, [fr.number for fr in frames])
    if not any(text.strip() for _, text in cells):
        raise RuntimeError(
            "нет закадрового текста на листе «план» (строка 49) — "
            "заполните ячейки по кадрам в project.xlsx (кадр 1 = col C)"
        )

    audio_dir = project.data_dir / "audio"

    async with browser_session() as bs:
        el = ElevenLabsBot(bs)
        clips, full_audio_path = await synthesize_per_frame_audio(
            el,
            project=project,
            frames=frames,
            cells=cells,
            audio_dir=audio_dir,
            whisper_model=settings.whisper_model,
        )

    for fr in frames:
        clip = next(c for c in clips if c.frame_number == fr.number)
        fr.start_ts = clip.start_ts
        fr.end_ts = clip.end_ts
        fr.duration_seconds = clip.duration

    audio_duration = await probe_duration(full_audio_path)
    expected = clips[-1].end_ts if clips else 0.0
    if abs(audio_duration - expected) > 0.15:
        logger.warning(
            "[#{}] voice_full duration {:.2f}s != sum clips {:.2f}s",
            project.id,
            audio_duration,
            expected,
        )

    clip_meta = [
        {
            "frame_number": c.frame_number,
            "start_ts": c.start_ts,
            "end_ts": c.end_ts,
            "duration": c.duration,
            "text": c.text,
        }
        for c in clips
    ]
    session.add(Artifact(
        project_id=project.id,
        kind=ArtifactKind.audio,
        uuid=uuid.uuid4().hex,
        path=str(full_audio_path),
        meta={"mode": "per_frame", "clip_count": len(clips), "clips": clip_meta},
    ))
    await session.flush()

    logger.info("[#{}] whisper on voice_full ({:.2f}s)", project.id, audio_duration)
    words = transcribe_words(
        full_audio_path,
        model_name=settings.whisper_model,
        language="ru",
    )
    frame_segments = [
        {
            "frame_number": clip.frame_number,
            "start_ts": clip.start_ts,
            "end_ts": clip.end_ts,
            "text": clip.text,
            "words": [
                {
                    "word": w.word,
                    "start": w.start,
                    "end": w.end,
                    "prob": w.prob,
                }
                for w in extract_local_frame_words(words, clip.start_ts, clip.end_ts)
            ],
        }
        for clip in clips
    ]
    words_path = audio_dir / f"words_{uuid.uuid4().hex[:8]}.json"
    dump_words_json(words, words_path, frames=frame_segments)
    session.add(Artifact(
        project_id=project.id,
        kind=ArtifactKind.whisper_words,
        uuid=uuid.uuid4().hex,
        path=str(words_path),
    ))

    logger.info(
        "[#{}] generate_audio done: {} frames, {:.2f}s total, {} whisper words",
        project.id,
        len(clips),
        clips[-1].end_ts if clips else 0.0,
        len(words),
    )

    project.status = ProjectStatus.audio_ready
    await session.flush()
