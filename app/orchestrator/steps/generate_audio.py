"""Шаг 10: озвучка plan R49 — вариант B (один mp3 на ячейку / кадр).

Готовый mp3 на диске → только Whisper (без 11Labs).
Иначе: TTS по ячейкам → voice_full → Whisper внутри synthesize_per_frame_audio.
"""

from __future__ import annotations

import asyncio
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
    recover_whisper_from_disk,
)
from app.services.frame_audio import (
    FrameAudioClip,
    align_existing_voice_full,
    find_voice_full_on_disk,
    synthesize_per_frame_audio,
)
from app.services.mapper import extract_local_frame_words
from app.services.media_probe import probe_duration
from app.services.asr import active_asr_backend
from app.services.whisper import WordTS, dump_words_json
from app.settings import settings
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells


async def _latest_artifact(
    session: AsyncSession,
    project_id: int,
    kind: ArtifactKind,
) -> Artifact | None:
    return (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind == kind,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _persist_audio_results(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    clips: list[FrameAudioClip],
    full_audio_path: Path,
    words: list[WordTS],
    audio_dir: Path,
    *,
    source: str,
) -> None:
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
        meta={
            "mode": "disk_whisper" if source == "disk_whisper" else "per_frame",
            "source": source,
            "clip_count": len(clips),
            "clips": clip_meta,
        },
    ))
    await session.flush()

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
        "[#{}] generate_audio done: {} frames, {:.2f}s total, {} whisper words ({})",
        project.id,
        len(clips),
        clips[-1].end_ts if clips else 0.0,
        len(words),
        source,
    )


async def _finalize_audio_ready(
    session: AsyncSession,
    project: Project,
) -> bool:
    from app.services.post_step_validate import finalize_or_retry

    if not await finalize_or_retry(
        session,
        project,
        step="audio",
        ready_status=ProjectStatus.audio_ready,
        running_status=ProjectStatus.generating_audio,
    ):
        return False

    project.status = ProjectStatus.audio_ready
    await session.flush()
    return True


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_audio:
        return
    logger.info("[#{}] generate_audio starting (per-frame TTS, plan R49)", project.id)

    await recover_scene_videos_from_disk(session, project)
    await recover_audio_from_disk(session, project)
    await recover_whisper_from_disk(session, project)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров")

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    voice_path = find_voice_full_on_disk(
        project.data_dir,
        meta=project.meta if isinstance(project.meta, dict) else None,
    )
    if voice_path is None:
        audio_art = await _latest_artifact(session, project.id, ArtifactKind.audio)
        if audio_art is not None and audio_art.path and Path(audio_art.path).is_file():
            voice_path = Path(audio_art.path)
    if voice_path is not None:
        logger.info(
            "[#{}] generate_audio: озвучка на диске → {}",
            project.id,
            voice_path,
        )
    else:
        logger.warning(
            "[#{}] generate_audio: файла озвучки нет в {} — {}",
            project.id,
            project.data_dir / "audio",
            "11Labs" if settings.audio_use_elevenlabs_fallback else "ошибка (11Labs выкл.)",
        )

    cells = read_plan_voiceover_cells(project, [fr.number for fr in frames])
    from app.services.frame_timeline_sync import timeline_frames_and_cells

    timeline_frames, cells = timeline_frames_and_cells(project, frames)
    if not timeline_frames:
        raise RuntimeError(
            "нет закадрового текста на листе «план» (R49) — заполните project.xlsx"
        )

    if voice_path is not None and voice_path.is_file():
        logger.info(
            "[#{}] generate_audio: озвучка на диске → {} — {} + align R49",
            project.id,
            voice_path,
            active_asr_backend(),
        )
        if active_asr_backend() == "nvidia":
            from app.services.nvidia_asr import ensure_nvidia_asr_ready

            logger.info(
                "[#{}] generate_audio: ждём Parakeet (~2.5 GB при первом запуске) …",
                project.id,
            )
            await asyncio.to_thread(ensure_nvidia_asr_ready)
        clips, full_audio_path, words = await align_existing_voice_full(
            project,
            timeline_frames,
            cells,
            voice_path,
            audio_dir,
            whisper_model=settings.whisper_model,
        )
        await _persist_audio_results(
            session,
            project,
            timeline_frames,
            clips,
            full_audio_path,
            words,
            audio_dir,
            source="disk_whisper",
        )
        await _finalize_audio_ready(session, project)
        return

    if not any(text.strip() for _, text in cells):
        raise RuntimeError(
            "нет закадрового текста (строка 49 / voiceover.txt / script_text) — "
            "положите готовый mp3/wav в audio/ или заполните текст"
        )

    if not settings.audio_use_elevenlabs_fallback:
        audio_hint = project.data_dir / "audio"
        raise RuntimeError(
            f"[#{project.id}] нет озвучки в {audio_hint} — положите voice.mp3 или "
            f"voice_full_*.wav (проект «{project.slug}»). "
            "11Labs отключён: AUDIO_USE_ELEVENLABS_FALLBACK=0"
        )

    async with browser_session() as bs:
        el = ElevenLabsBot(bs)
        clips, full_audio_path, words = await synthesize_per_frame_audio(
            el,
            project=project,
            frames=timeline_frames,
            cells=cells,
            audio_dir=audio_dir,
            whisper_model=settings.whisper_model,
        )

    await _persist_audio_results(
        session,
        project,
        timeline_frames,
        clips,
        full_audio_path,
        words,
        audio_dir,
        source="elevenlabs",
    )
    await _finalize_audio_ready(session, project)
