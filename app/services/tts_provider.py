"""Общий выбор провайдера озвучки: прямой ElevenLabs или WaveSpeed Eleven V4.

Дефолт без настройки — elevenlabs (как раньше). WaveSpeed включается явно:
AUDIO_TTS_PROVIDER=wavespeed_eleven_v4 или meta.node_step_params.audio.tts_provider.
Ключи остаются на сервере.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

from app.models import Project
from app.services.elevenlabs_api import (
    elevenlabs_api_configured,
)
from app.services.elevenlabs_api import (
    synthesize_speech as synthesize_elevenlabs,
)
from app.services.elevenlabs_voices import resolve_elevenlabs_voice_id
from app.services.wavespeed_eleven_v4 import (
    WaveSpeedElevenV4Error,
    resolve_wavespeed_voice_settings,
    wavespeed_api_configured,
)
from app.services.wavespeed_eleven_v4 import (
    synthesize_speech as synthesize_wavespeed,
)
from app.settings import settings

PROVIDER_ELEVENLABS = "elevenlabs"
PROVIDER_WAVESPEED_ELEVEN_V4 = "wavespeed_eleven_v4"

_PROVIDER_ALIASES = {
    "elevenlabs": PROVIDER_ELEVENLABS,
    "11labs": PROVIDER_ELEVENLABS,
    "eleven_labs": PROVIDER_ELEVENLABS,
    "wavespeed": PROVIDER_WAVESPEED_ELEVEN_V4,
    "wavespeed_eleven_v4": PROVIDER_WAVESPEED_ELEVEN_V4,
    "eleven_v4": PROVIDER_WAVESPEED_ELEVEN_V4,
    "elevenlabs_eleven_v4": PROVIDER_WAVESPEED_ELEVEN_V4,
}


class TtsError(RuntimeError):
    """Некорректный провайдер или отказ синтеза речи."""


def normalize_tts_provider(value: str | None) -> str:
    """Канонический id провайдера. Пустое значение — elevenlabs."""
    raw = str(value or "").strip().lower().replace("-", "_")
    if not raw:
        return PROVIDER_ELEVENLABS
    provider = _PROVIDER_ALIASES.get(raw)
    if provider is None:
        raise TtsError(
            f"Неизвестный провайдер озвучки «{value}». "
            "Допустимо: elevenlabs или wavespeed_eleven_v4."
        )
    return provider


def _audio_bucket(project: Project | None) -> dict:
    meta = getattr(project, "meta", None) or {}
    if not isinstance(meta, dict):
        return {}
    raw = meta.get("node_step_params")
    if not isinstance(raw, dict):
        return {}
    audio = raw.get("audio")
    return audio if isinstance(audio, dict) else {}


def resolve_tts_provider(project: Project | None = None) -> str:
    """Сначала выбор ноды озвучки, иначе AUDIO_TTS_PROVIDER, иначе elevenlabs."""
    chosen = _audio_bucket(project).get("tts_provider")
    if isinstance(chosen, str) and chosen.strip():
        return normalize_tts_provider(chosen)
    env_value = getattr(settings, "audio_tts_provider", None) or os.environ.get(
        "AUDIO_TTS_PROVIDER", ""
    )
    return normalize_tts_provider(str(env_value or ""))


def elevenlabs_tts_enabled() -> bool:
    """Старый шлюз: есть ключ ElevenLabs или явно включён fallback."""
    flag = os.environ.get("AUDIO_USE_ELEVENLABS_FALLBACK", "").strip()
    return bool(
        getattr(settings, "audio_use_elevenlabs_fallback", False)
        or flag in ("1", "true", "True")
        or getattr(settings, "elevenlabs_api_key", None)
    )


async def synthesize_project_audio(
    text: str,
    out_path: Path,
    *,
    project: Project,
    timeout: float,
    el: Any | None = None,
) -> str:
    """Озвучивает текст выбранным провайдером и возвращает его id."""
    provider = resolve_tts_provider(project)
    if provider == PROVIDER_WAVESPEED_ELEVEN_V4:
        if not wavespeed_api_configured():
            raise WaveSpeedElevenV4Error(
                "Провайдер озвучки WaveSpeed Eleven V4 выбран, но не задан "
                "WAVESPEED_API_KEY в .env."
            )
        voice_id, stability, similarity = resolve_wavespeed_voice_settings(project)
        logger.info(
            "[#{}] TTS provider=wavespeed_eleven_v4 voice_id={}",
            getattr(project, "id", "?"),
            voice_id,
        )
        await synthesize_wavespeed(
            text,
            out_path,
            voice_id=voice_id,
            stability=stability,
            similarity=similarity,
            timeout=timeout,
        )
        return provider

    voice_id = resolve_elevenlabs_voice_id(project)
    logger.info(
        "[#{}] TTS provider=elevenlabs voice_id={}",
        getattr(project, "id", "?"),
        voice_id,
    )
    if elevenlabs_api_configured() or el is None:
        await synthesize_elevenlabs(
            text,
            out_path,
            voice_id=voice_id,
            timeout=timeout,
        )
    else:
        await el.tts(
            text,
            out_path,
            timeout=timeout,
            voice_id=voice_id,
            project_id=getattr(project, "id", None),
        )
    return provider
