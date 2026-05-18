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
from app.bots.chatgpt import ChatGPTBot
from app.bots.elevenlabs import ElevenLabsBot
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    Project,
    ProjectStatus,
)
from app.services.gpt_check import (
    GptCheckDecision,
    gpt_check_file_artifact,
    load_check_prompt,
)
from app.services.mapper import map_frames
from app.services.whisper import dump_words_json, transcribe_words
from app.settings import settings

_AUDIO_GPT_MAX_RETRIES = 5


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

    script_text = "\n".join(fr.voiceover_text.strip() for fr in frames if fr.voiceover_text)
    if not script_text:
        raise RuntimeError("пустой сценарий для озвучки")

    audio_dir = project.data_dir / "audio"
    audio_path = audio_dir / f"voice_{uuid.uuid4().hex[:8]}.mp3"

    # (Фаза 7) GPT-проверка аудио.
    try:
        _audio_check_prompt = load_check_prompt("audio")
    except FileNotFoundError:
        _audio_check_prompt = None
        logger.warning("[#{}] промт check_audio не найден, пропускаю GPT-check", project.id)

    async with browser_session() as bs:
        el = ElevenLabsBot(bs)
        gpt = ChatGPTBot(bs)

        await el.tts(script_text, audio_path, timeout=600)

        # (Фаза 7) GPT-проверка аудио — до 5 перегенераций.
        if _audio_check_prompt and audio_path.exists():
            for audio_attempt in range(1, _AUDIO_GPT_MAX_RETRIES + 1):
                check_result = await gpt_check_file_artifact(
                    chatgpt_bot=gpt,
                    check_prompt=_audio_check_prompt,
                    artifact_path=audio_path,
                    new_conversation=True,
                    timeout=1200.0,
                )
                logger.info(
                    "[#{}] audio GPT-check {}/{}: decision={}",
                    project.id, audio_attempt, _AUDIO_GPT_MAX_RETRIES,
                    check_result.decision.value,
                )
                if check_result.decision is not GptCheckDecision.regenerate:
                    break
                if audio_attempt >= _AUDIO_GPT_MAX_RETRIES:
                    logger.warning(
                        "[#{}] audio GPT-check: {} попыток исчерпано",
                        project.id, _AUDIO_GPT_MAX_RETRIES,
                    )
                    break
                try:
                    audio_path.unlink(missing_ok=True)
                except OSError:
                    pass
                audio_path = audio_dir / f"voice_{uuid.uuid4().hex[:8]}.mp3"
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

    # реальные таймкоды кадров
    cells = [(fr.number, fr.voiceover_text or "") for fr in frames]
    timings = map_frames(cells, words)
    by_num = {t.frame_number: t for t in timings}
    for fr in frames:
        t = by_num.get(fr.number)
        if t and t.duration > 0:
            fr.start_ts = t.start_ts
            fr.end_ts = t.end_ts
            fr.duration_seconds = t.duration

    project.status = ProjectStatus.audio_ready
    await session.flush()
    logger.info("[#{}] generate_audio done, {} слов, последний кадр заканчивается на {:.2f}с",
                project.id, len(words),
                max((fr.end_ts or 0.0) for fr in frames))
