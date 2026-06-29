"""Маршрутизация ASR: nvidia (NeMo Parakeet, CUDA) или whisper."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.services.whisper import WordTS
from app.settings import settings


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def require_nvidia_cuda() -> None:
    """Жёстко: montage ASR только NeMo + CUDA."""
    backend = (settings.asr_backend or "nvidia").strip().lower()
    if backend != "nvidia":
        raise RuntimeError(
            f"ASR_BACKEND={backend!r}, нужен nvidia. В .env: ASR_BACKEND=nvidia"
        )
    if not cuda_available():
        raise RuntimeError(
            "CUDA недоступна (torch.cuda.is_available()=False). "
            "Проверь драйвер NVIDIA и torch с CUDA: "
            "python -c \"import torch; print(torch.cuda.is_available(), torch.version.cuda)\""
        )


def effective_asr_backend() -> str:
    configured = (settings.asr_backend or "nvidia").strip().lower()
    if configured == "nvidia":
        require_nvidia_cuda()
        return "nvidia"
    return configured


def asr_available() -> bool:
    if effective_asr_backend() == "nvidia":
        from app.services.asr.nvidia_backend import nvidia_asr_available

        return nvidia_asr_available()
    from app.services.asr.whisper_backend import whisper_available

    return whisper_available()


def get_asr_backend_label() -> str:
    backend = effective_asr_backend()
    if backend == "nvidia":
        try:
            import torch

            gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "?"
        except Exception:  # noqa: BLE001
            gpu = "?"
        return f"nvidia:{settings.nvidia_asr_model} (cuda:{gpu})"
    return f"whisper:{settings.whisper_model}"


def transcribe_words(
    audio_path: Path,
    *,
    model_name: str | None = None,
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
) -> list[WordTS]:
    backend = effective_asr_backend()
    name = model_name or (
        settings.nvidia_asr_model
        if backend == "nvidia"
        else settings.whisper_model
    )
    logger.info("asr: backend={} file={}", get_asr_backend_label(), audio_path.name)
    if backend == "nvidia":
        from app.services.asr import nvidia_backend as mod

        return mod.transcribe_words(
            audio_path,
            model_name=name,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
    from app.services.asr import whisper_backend as mod

    return mod.transcribe_words(
        audio_path,
        model_name=name,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )


def transcribe_words_many(
    audio_paths: list[Path],
    *,
    model_name: str | None = None,
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
) -> list[list[WordTS]]:
    if not audio_paths:
        return []
    backend = effective_asr_backend()
    name = model_name or (
        settings.nvidia_asr_model
        if backend == "nvidia"
        else settings.whisper_model
    )
    if backend == "nvidia":
        from app.services.asr import nvidia_backend as mod

        return mod.transcribe_words_many(
            audio_paths,
            model_name=name,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
    from app.services.asr import whisper_backend as mod

    return mod.transcribe_words_many(
        audio_paths,
        model_name=name,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
