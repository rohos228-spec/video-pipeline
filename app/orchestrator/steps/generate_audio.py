"""Шаг 10: озвучка всего сценария через 11Labs web → один mp3.
Затем faster-whisper делает word-level таймкоды и мы проставляем реальные
start_ts/end_ts на Frame + лист «план» (R49 voiceover, R51 конец слова).
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
from app.services.assembly_inputs import resolve_voice_path
from app.services.mapper import map_frames
from app.services.whisper import dump_words_json, transcribe_words
from app.services.xlsx_v8_plan import (
    column_map,
    plan_columns_to_cells,
    read_plan_columns,
    write_whisper_timecodes,
)
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_audio:
        return
    logger.info("[#{}] generate_audio starting", project.id)

    xlsx_path = project.data_dir / "project.xlsx"
    plan_columns = None
    cells: list[tuple[int, str]] = []

    if xlsx_path.exists():
        try:
            plan_columns = read_plan_columns(xlsx_path)
            cells = plan_columns_to_cells(plan_columns)
            logger.info(
                "[#{}] xlsx «план»: {} блоков закадрового (R49)",
                project.id,
                len(cells),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[#{}] xlsx plan read failed: {}", project.id, e)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров")

    if not cells:
        cells = [(fr.number, fr.voiceover_text or "") for fr in frames]

    script_text = "\n".join(t for _, t in cells if t.strip())
    if not script_text:
        raise RuntimeError("пустой сценарий для озвучки")

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    existing_voice = resolve_voice_path(project, None)
    if existing_voice is not None:
        audio_path = existing_voice
        logger.info("[#{}] используем готовую озвучку: {}", project.id, audio_path)
    else:
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

    timings = map_frames(cells, words)
    by_num = {t.frame_number: t for t in timings}
    for fr in frames:
        t = by_num.get(fr.number)
        if t and t.duration > 0:
            fr.start_ts = t.start_ts
            fr.end_ts = t.end_ts
            fr.duration_seconds = t.duration

    if xlsx_path.exists() and timings:
        try:
            col_map = column_map(plan_columns) if plan_columns else None
            write_whisper_timecodes(
                xlsx_path,
                [
                    (t.frame_number, t.start_ts, t.end_ts, t.duration)
                    for t in timings
                ],
                column_by_frame=col_map,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[#{}] xlsx timecode write failed: {}", project.id, e)

    project.status = ProjectStatus.audio_ready
    await session.flush()
    logger.info(
        "[#{}] generate_audio done, {} слов, последний кадр {:.2f}с",
        project.id,
        len(words),
        max((fr.end_ts or 0.0) for fr in frames),
    )
