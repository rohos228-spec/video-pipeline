"""Шаг 11: финальная сборка — видео и субтитры только по озвучке."""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    HITLKind,
    Project,
    ProjectStatus,
)
from app.services.assembly import ClipSpec, assemble, make_simple_ass
from app.services.bgm import resolve_bgm
from app.services.frame_audio import build_assembly_timeline
from app.services.hitl import send_hitl_video
from app.services.mapper import FrameTiming
from app.services.subtitles import build_subtitle_cues_from_cells
from app.services.whisper import WordTS, load_words_json, transcribe_words, whisper_available
from app.settings import settings
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells


def _scale_whisper_words(words: list[WordTS], factor: float) -> list[WordTS]:
    if abs(factor - 1.0) < 0.001:
        return words
    return [
        WordTS(
            word=w.word,
            start=round(w.start * factor, 3),
            end=round(w.end * factor, 3),
            prob=w.prob,
        )
        for w in words
    ]


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.assembling:
        return
    logger.info("[#{}] assemble starting", project.id)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров")

    audio = (
        await session.execute(
            select(Artifact)
            .where(Artifact.project_id == project.id, Artifact.kind == ArtifactKind.audio)
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if audio is None:
        raise RuntimeError("нет артефакта аудио")

    audio_path = Path(audio.path)
    audio_dir = project.data_dir / "audio"
    frame_numbers = [fr.number for fr in frames]
    per_frame_tts = (audio.meta or {}).get("mode") == "per_frame"

    whisper_art = (
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
    if whisper_art is None:
        raise RuntimeError(
            "нет word-level таймкодов Whisper — перезапустите шаг «Аудио» перед сборкой"
        )
    words = load_words_json(Path(whisper_art.path))
    if not words:
        raise RuntimeError("Whisper не вернул слова для субтитров")

    if settings.subtitle_rewhisper_on_assemble and audio_path.is_file():
        if whisper_available():
            logger.info("[#{}] assemble: re-whisper voice_full для субтитров (без TTS)", project.id)
            words = transcribe_words(
                audio_path,
                model_name=settings.whisper_model,
                language="ru",
            )
        else:
            logger.warning(
                "[#{}] assemble: faster-whisper не установлен — субтитры из words.json. "
                "Для re-whisper: pip install -e \".[whisper]\"",
                project.id,
            )

    cells = read_plan_voiceover_cells(project, frame_numbers)
    if not any(text.strip() for _, text in cells):
        raise RuntimeError(
            "нет текста на листе «план» (строка 49) — одна ячейка = одно видео"
        )

    try:
        audio_clips, audio_duration, time_scale, per_frame_audio = await build_assembly_timeline(
            audio_dir,
            audio_path,
            frame_numbers,
            cells=cells,
            words=words,
            per_frame_tts=per_frame_tts,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc

    words = _scale_whisper_words(words, time_scale) if not per_frame_audio else words

    video_duration = sum(c.duration for c in audio_clips)
    logger.info(
        "[#{}] assemble: master voice {:.2f}s, {} clips, video {:.2f}s, subtitles={}",
        project.id,
        audio_duration,
        len(audio_clips),
        video_duration,
        "per-frame" if per_frame_audio else "legacy-stretch",
    )

    duration_by_frame = {c.frame_number: c.duration for c in audio_clips}
    frame_timings = [
        FrameTiming(c.frame_number, c.start_ts, c.end_ts, c.duration)
        for c in audio_clips
    ]

    clips: list[ClipSpec] = []
    for fr in frames:
        video_art = (
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
        if video_art is None:
            raise RuntimeError(f"нет клипа для кадра {fr.number}")
        duration = duration_by_frame[fr.number]
        clips.append(ClipSpec(src=Path(video_art.path), duration=duration))
        fr.start_ts = next(c.start_ts for c in audio_clips if c.frame_number == fr.number)
        fr.end_ts = next(c.end_ts for c in audio_clips if c.frame_number == fr.number)
        fr.duration_seconds = duration

    subs_dir = project.data_dir / "subs"
    subs_path = subs_dir / f"subs_{uuid.uuid4().hex[:8]}.ass"
    sub_entries = build_subtitle_cues_from_cells(
        cells,
        words,
        frame_timings,
        max_words=settings.subtitle_max_words,
        max_end_ts=audio_duration,
        lead_seconds=settings.subtitle_lead_seconds,
        chars_per_second=settings.subtitle_chars_per_second,
    )
    if not sub_entries:
        raise RuntimeError("не удалось построить субтитры из Excel + Whisper")
    make_simple_ass(sub_entries, subs_path)
    session.add(Artifact(
        project_id=project.id, kind=ArtifactKind.subtitle,
        uuid=uuid.uuid4().hex, path=str(subs_path),
    ))

    out_dir = project.data_dir / "final"
    out_path = out_dir / f"{project.slug}.mp4"
    bgm = resolve_bgm(project)
    await assemble(
        clips,
        audio_path,
        out_path,
        subtitles_ass=subs_path,
        max_duration=audio_duration,
        bgm=bgm,
    )
    await session.flush()

    session.add(Artifact(
        project_id=project.id, kind=ArtifactKind.final_video,
        uuid=uuid.uuid4().hex, path=str(out_path),
    ))
    project.status = ProjectStatus.assembled
    await session.flush()

    await send_hitl_video(
        bot, session, project,
        kind=HITLKind.approve_final,
        video_path=str(out_path),
        caption=f"Финальный ролик #{project.id} готов. Одобрить и публиковать?",
        payload={"step": "final"},
    )
