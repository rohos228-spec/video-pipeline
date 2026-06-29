"""faster-whisper backend с поддержкой CUDA."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.services.whisper import WordTS
from app.settings import settings

_WHISPER_INSTALL_HINT = 'pip install -e ".[whisper]"'


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


_model_cache: dict[tuple[str, str, str], object] = {}


def _create_model(model_name: str, device: str, compute_type: str):
    key = (model_name, device, compute_type)
    if key in _model_cache:
        return _model_cache[key]
    from faster_whisper import WhisperModel

    logger.info("whisper: loading '{}' device={} compute={}", model_name, device, compute_type)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    _model_cache[key] = model
    return model


def _resolve_runtime(
    device: str | None,
    compute_type: str | None,
) -> tuple[str, str]:
    if device and compute_type:
        return device, compute_type
    return settings.whisper_device, settings.whisper_compute_type


def _segments_to_words(segments) -> list[WordTS]:
    words: list[WordTS] = []
    for seg in segments:
        for w in seg.words or []:
            words.append(
                WordTS(
                    word=w.word.strip(),
                    start=float(w.start),
                    end=float(w.end),
                    prob=float(getattr(w, "probability", 0.0)),
                )
            )
    return words


def transcribe_words(
    audio_path: Path,
    *,
    model_name: str = "medium",
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
    device: str | None = None,
    compute_type: str | None = None,
) -> list[WordTS]:
    if not whisper_available():
        raise ImportError(f"faster-whisper не установлен. {_WHISPER_INSTALL_HINT}")
    dev, ctype = _resolve_runtime(device, compute_type)
    model = _create_model(model_name, dev, ctype)
    segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        word_timestamps=True,
        vad_filter=vad_filter,
    )
    words = _segments_to_words(segments)
    logger.info("whisper: got {} words", len(words))
    return words


def transcribe_words_many(
    audio_paths: list[Path],
    *,
    model_name: str = "medium",
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
    device: str | None = None,
    compute_type: str | None = None,
) -> list[list[WordTS]]:
    if not audio_paths:
        return []
    if not whisper_available():
        raise ImportError(f"faster-whisper не установлен. {_WHISPER_INSTALL_HINT}")
    dev, ctype = _resolve_runtime(device, compute_type)
    model = _create_model(model_name, dev, ctype)
    out: list[list[WordTS]] = []
    for audio_path in audio_paths:
        logger.info("whisper: transcribing {}", audio_path)
        segments, _info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            word_timestamps=True,
            vad_filter=vad_filter,
        )
        out.append(_segments_to_words(segments))
    return out
