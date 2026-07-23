"""ASR facade: NVIDIA Parakeet (default) или faster-whisper fallback."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.services.whisper import WordTS, transcribe_words_whisper, transcribe_words_many_whisper
from app.settings import settings


def active_asr_backend() -> str:
    return (settings.asr_backend or "whisper").strip().lower()


def _resolve_nvidia_model(explicit: str | None) -> str:
    """NVIDIA backend всегда берёт Parakeet — не WHISPER_MODEL (large-v3)."""
    candidate = (explicit or "").strip()
    if candidate.startswith("nvidia/") or "parakeet" in candidate.lower():
        return candidate
    if candidate and candidate != settings.nvidia_asr_model:
        logger.debug(
            "nvidia ASR: model_name={!r} — это whisper-модель, "
            "используем NVIDIA_ASR_MODEL={}",
            candidate,
            settings.nvidia_asr_model,
        )
    return settings.nvidia_asr_model


def transcribe_words(
    audio_path: Path,
    *,
    model_name: str | None = None,
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
    device: str | None = None,
    compute_type: str | None = None,
) -> list[WordTS]:
    backend = active_asr_backend()
    if backend == "nvidia":
        from app.services.nvidia_asr import nvidia_asr_available, transcribe_words_nvidia

        if nvidia_asr_available():
            nvidia_model = _resolve_nvidia_model(model_name)
            return transcribe_words_nvidia(
                audio_path,
                model_name=nvidia_model,
                language=language,
            )
        logger.warning(
            "ASR_BACKEND=nvidia, но NeMo не установлен — fallback на whisper. "
            'Установите: pip install -e ".[nvidia]"'
        )

    whisper_model = model_name or settings.whisper_model
    return transcribe_words_whisper(
        audio_path,
        model_name=whisper_model,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
        device=device,
        compute_type=compute_type,
    )


def transcribe_words_many(
    audio_paths: list[Path],
    *,
    model_name: str | None = None,
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
    device: str | None = None,
    compute_type: str | None = None,
) -> list[list[WordTS]]:
    backend = active_asr_backend()
    if backend == "nvidia":
        from app.services.nvidia_asr import nvidia_asr_available, transcribe_words_many_nvidia

        if nvidia_asr_available():
            nvidia_model = _resolve_nvidia_model(model_name)
            return transcribe_words_many_nvidia(
                audio_paths,
                model_name=nvidia_model,
                language=language,
            )
        logger.warning("ASR_BACKEND=nvidia недоступен — batch fallback whisper")

    whisper_model = model_name or settings.whisper_model
    return transcribe_words_many_whisper(
        audio_paths,
        model_name=whisper_model,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
        device=device,
        compute_type=compute_type,
    )
