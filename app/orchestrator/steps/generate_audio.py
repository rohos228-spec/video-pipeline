"""Шаг 10: озвучка всего сценария через 11Labs web → один mp3.

Что делает шаг:
  1. Берёт голос из топика (столбец U topics.xlsx → meta["topic_card"]["voice"]).
     По имени голоса ищет URL в `prompts/voices.json` (см. `app/services/voices.py`).
  2. Берёт «сырой» закадровый текст из `data/videos/<slug>/voiceover.txt`
     (формируется шагом 2 — make_script). Если файла нет — fallback на
     склейку Frame.voiceover_text всех кадров через `\\n`.
  3. Через Dolphin Anty (`app/bots/dolphin.py`) открывает 11labs, заходит на
     URL голоса, вставляет текст, жмёт Generate, скачивает mp3.
  4. Прогоняет mp3 через faster-whisper, проставляет реальные start_ts/end_ts
     на Frame'ах.

Где хранится результат:
  - mp3 — в `data_dir/audio/voice_<hex>.mp3`, плюс запись в Artifact (audio).
  - whisper words.json — в той же папке + Artifact (whisper_words).
"""

from __future__ import annotations

import uuid

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.dolphin import dolphin_session
from app.bots.elevenlabs import ElevenLabsBot
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    Project,
    ProjectStatus,
)
from app.services.mapper import map_frames
from app.services.voices import find_voice
from app.services.whisper import dump_words_json, transcribe_words
from app.settings import settings


def _read_voiceover_txt(project: Project) -> str | None:
    """Прочитать `<data_dir>/voiceover.txt`. None если файла нет/пустой."""
    path = project.data_dir / "voiceover.txt"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] voiceover.txt чтение упало: {}", project.id, e)
        return None
    return text or None


def _topic_voice_name(project: Project) -> str | None:
    """Имя голоса из карточки топика (столбец U topics.xlsx)."""
    meta = project.meta or {}
    card = meta.get("topic_card") or {}
    name = card.get("voice")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_audio:
        return
    logger.info("[#{}] generate_audio starting", project.id)

    # 1) Текст: сырой voiceover.txt из шага 2, fallback — склейка Frame.voiceover_text.
    script_text = _read_voiceover_txt(project)
    if script_text:
        logger.info(
            "[#{}] generate_audio: использую raw voiceover.txt ({} симв.)",
            project.id, len(script_text),
        )
    else:
        frames = (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars().all()
        if not frames:
            raise RuntimeError("нет кадров и нет voiceover.txt — нечего озвучивать")
        script_text = "\n".join(
            fr.voiceover_text.strip() for fr in frames if fr.voiceover_text
        )
        if not script_text:
            raise RuntimeError("пустой сценарий для озвучки")
        logger.info(
            "[#{}] generate_audio: voiceover.txt не найден, использую склейку "
            "Frame.voiceover_text ({} симв.)",
            project.id, len(script_text),
        )

    # 2) Голос: имя из топика (U) → URL из prompts/voices.json.
    voice_name = _topic_voice_name(project)
    voice = find_voice(voice_name)
    voice_url: str | None = None
    if voice:
        voice_url = voice.url
        logger.info("[#{}] generate_audio: голос '{}' → {}", project.id, voice.name, voice.url)
    elif voice_name:
        logger.warning(
            "[#{}] generate_audio: голос '{}' не найден в prompts/voices.json, "
            "открываю дефолтную страницу 11labs",
            project.id, voice_name,
        )
    else:
        logger.info(
            "[#{}] generate_audio: голос в топике не задан (U в topics.xlsx), "
            "открываю дефолтную страницу 11labs",
            project.id,
        )

    # 3) TTS через Dolphin Anty + 11labs
    audio_dir = project.data_dir / "audio"
    audio_path = audio_dir / f"voice_{uuid.uuid4().hex[:8]}.mp3"
    async with dolphin_session() as bs:
        el = ElevenLabsBot(bs)
        await el.tts(script_text, audio_path, voice_url=voice_url, timeout=600)

    session.add(Artifact(
        project_id=project.id,
        kind=ArtifactKind.audio,
        uuid=uuid.uuid4().hex,
        path=str(audio_path),
    ))
    await session.flush()

    # 4) Whisper: словные таймкоды → реальные start_ts/end_ts на Frame'ах.
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

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
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
    logger.info(
        "[#{}] generate_audio done, {} слов, последний кадр заканчивается на {:.2f}с",
        project.id, len(words),
        max((fr.end_ts or 0.0) for fr in frames) if frames else 0.0,
    )
