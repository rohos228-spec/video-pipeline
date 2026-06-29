"""ASR: word-level таймкоды (whisper / NVIDIA Parakeet через app.services.asr)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger


@dataclass
class WordTS:
    word: str
    start: float
    end: float
    prob: float = 0.0


def whisper_available() -> bool:
    from app.services.asr.engine import asr_available

    return asr_available()


_WHISPER_INSTALL_HINT = (
    'pip install -e ".[whisper]"   # или NVIDIA: pip install -e ".[nvidia-asr]"'
)


def transcribe_words(
    audio_path: Path,
    *,
    model_name: str = "medium",
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
) -> list[WordTS]:
    """Word-level таймкоды; vad_filter=False — сохраняет паузы между словами."""
    from app.services.asr.engine import transcribe_words as _transcribe

    return _transcribe(
        audio_path,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )


def transcribe_words_many(
    audio_paths: list[Path],
    *,
    model_name: str = "medium",
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
) -> list[list[WordTS]]:
    """ASR для нескольких файлов — модель грузится один раз."""
    from app.services.asr.engine import transcribe_words_many as _transcribe_many

    return _transcribe_many(
        audio_paths,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )


def dump_words_json(
    words: list[WordTS],
    path: Path,
    *,
    frames: list[dict] | None = None,
) -> None:
    """Сохранить word-level таймкоды; frames — границы ячеек R49 (per-frame TTS)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if frames is None:
        payload: list | dict = [asdict(w) for w in words]
    else:
        payload = {
            "mode": "per_frame",
            "words": [asdict(w) for w in words],
            "frames": frames,
        }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_words_json(path: Path) -> list[WordTS]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [WordTS(**row) for row in data]
    return [WordTS(**row) for row in data["words"]]
