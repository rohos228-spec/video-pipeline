"""ASR перед монтажом: words.json для субтитров. Тайминги монтажа — только Excel R15."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Project
from app.services.asr import active_asr_backend, transcribe_words
from app.services.asr_audio_prep import prepare_audio_for_asr
from app.services.nvidia_asr import nvidia_asr_available
from app.services.whisper import WordTS, dump_words_json, whisper_available
from app.settings import settings
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells


def asr_available() -> bool:
    if active_asr_backend() == "nvidia":
        return nvidia_asr_available()
    return whisper_available()


def get_asr_backend_label() -> str:
    backend = active_asr_backend()
    if backend == "nvidia":
        try:
            import torch

            gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "?"
        except Exception:  # noqa: BLE001
            gpu = "?"
        return f"nvidia:{settings.nvidia_asr_model} (cuda:{gpu})"
    return f"whisper:{settings.whisper_model}"


async def ensure_montage_words(
    session: AsyncSession,
    project: Project,
    *,
    audio_path: Path,
    audio_dir: Path,
    frame_numbers: list[int],
    existing_words: list[WordTS] | None = None,
) -> list[WordTS]:
    """Word timestamps для субтитров. Не трогает Excel и не считает таймлайн монтажа."""
    words = list(existing_words or [])
    if words:
        return words

    if not asr_available():
        raise RuntimeError(
            f"ASR не установлен ({settings.asr_backend}). "
            'На ПК монтажа: pip install -e ".[nvidia-asr]" или ".[whisper]"'
        )

    logger.info(
        "[#{}] montage ASR (subs only): {} → {}",
        project.id,
        get_asr_backend_label(),
        audio_path.name,
    )
    asr_path = await prepare_audio_for_asr(audio_path)
    words = await asyncio.to_thread(transcribe_words, asr_path, language="ru")
    if not words:
        raise RuntimeError("ASR не вернул слова — проверьте voice_full.mp3")

    cells = read_plan_voiceover_cells(project, frame_numbers)
    if not any(t.strip() for _, t in cells):
        raise RuntimeError(
            "не удалось прочитать текст кадров из project.xlsx (лист «план», строка 49)"
        )

    words_path = audio_dir / f"words_{uuid.uuid4().hex[:8]}.json"
    dump_words_json(words, words_path)
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.whisper_words,
            uuid=uuid.uuid4().hex,
            path=str(words_path),
            meta={"source": get_asr_backend_label(), "montage": True, "subs_only": True},
        )
    )
    await session.flush()
    return words
