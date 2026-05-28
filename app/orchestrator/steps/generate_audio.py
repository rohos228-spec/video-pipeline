"""Шаг 10: озвучка всего сценария через 11Labs web → один mp3.
Затем faster-whisper делает word-level таймкоды и мы проставляем реальные
start_ts/end_ts на Frame.
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
from app.services.mapper import map_frames, tokenize_lower
from app.services.media_probe import probe_duration
from app.services.whisper import dump_words_json, transcribe_words
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_audio:
        return
    logger.info("[#{}] generate_audio starting", project.id)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров")

    cells = read_plan_voiceover_cells(project, [fr.number for fr in frames])
    script_text = "\n".join(text for _, text in cells if text.strip())
    if not script_text:
        raise RuntimeError(
            "нет закадрового текста на листе «план» (строка 49) — "
            "заполните ячейки по кадрам в project.xlsx"
        )

    audio_dir = project.data_dir / "audio"
    audio_path = audio_dir / f"voice_{uuid.uuid4().hex[:8]}.mp3"

    async with browser_session() as bs:
        el = ElevenLabsBot(bs)
        await el.tts(script_text, audio_path, timeout=600)

    session.add(Artifact(
        project_id=project.id,
        kind=ArtifactKind.audio,
        uuid=uuid.uuid4().hex,
        path=str(audio_path),
    ))
    await session.flush()

    # whisper
    logger.info("[#{}] whisper transcribe starting", project.id)
    words = transcribe_words(audio_path, language="ru", model_name=settings.whisper_model)
    words_path = audio_dir / f"words_{uuid.uuid4().hex[:8]}.json"
    dump_words_json(words, words_path)
    session.add(Artifact(
        project_id=project.id,
        kind=ArtifactKind.whisper_words,
        uuid=uuid.uuid4().hex,
        path=str(words_path),
    ))

    # реальные таймкоды кадров (текст — только plan R49, одна ячейка = одно видео)
    audio_duration = await probe_duration(audio_path)
    script_word_count = sum(len(tokenize_lower(text)) for _, text in cells)
    logger.info(
        "[#{}] whisper alignment: script_tokens={} whisper_words={} audio={:.2f}s",
        project.id,
        script_word_count,
        len(words),
        audio_duration,
    )
    if abs(script_word_count - len(words)) > max(3, script_word_count // 10):
        logger.warning(
            "[#{}] whisper/script word count mismatch ({} vs {}) — "
            "timings use fuzzy alignment + proportional fill",
            project.id,
            script_word_count,
            len(words),
        )
    timings = map_frames(cells, words, audio_duration=audio_duration)
    by_num = {t.frame_number: t for t in timings}
    zero_dur = 0
    for fr in frames:
        t = by_num.get(fr.number)
        if t is None:
            zero_dur += 1
            continue
        fr.start_ts = t.start_ts
        fr.end_ts = t.end_ts
        fr.duration_seconds = t.duration
        if t.duration <= 0:
            zero_dur += 1

    if zero_dur:
        logger.warning(
            "[#{}] generate_audio: {} frames have zero whisper weight — "
            "timings redistributed across full audio ({:.2f}s)",
            project.id,
            zero_dur,
            audio_duration,
        )

    project.status = ProjectStatus.audio_ready
    await session.flush()
    logger.info("[#{}] generate_audio done, {} слов, последний кадр заканчивается на {:.2f}с",
                project.id, len(words),
                max((fr.end_ts or 0.0) for fr in frames))
