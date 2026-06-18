"""faster-whisper: транскрибация озвучки в список слов с таймкодами."""

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
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


_WHISPER_INSTALL_HINT = (
    'pip install -e ".[whisper]"   # или: pip install "faster-whisper>=1.0"'
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
    if not whisper_available():
        raise ImportError(f"faster-whisper не установлен. {_WHISPER_INSTALL_HINT}")
    from faster_whisper import WhisperModel  # ленивый импорт — тяжёлая зависимость
    import time

    logger.info("whisper: loading model '{}' ...", model_name)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    logger.info(
        "whisper: transcribing {} (vad_filter={}, beam={}) — на CPU это может занять "
        "несколько минут для длинного файла",
        audio_path.name,
        vad_filter,
        beam_size,
    )
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        word_timestamps=True,
        vad_filter=vad_filter,
    )
    duration_hint = float(getattr(info, "duration", 0.0) or 0.0)
    if duration_hint > 0:
        logger.info("whisper: длительность аудио {:.1f}s", duration_hint)

    words: list[WordTS] = []
    last_log = time.monotonic()
    seg_count = 0
    for seg in segments:
        seg_count += 1
        for w in seg.words or []:
            words.append(WordTS(
                word=w.word.strip(),
                start=float(w.start),
                end=float(w.end),
                prob=float(getattr(w, "probability", 0.0)),
            ))
        now = time.monotonic()
        if now - last_log >= 30.0:
            logger.info(
                "whisper: … {} сегм., {} слов, до {:.0f}s аудио",
                seg_count,
                len(words),
                float(seg.end),
            )
            last_log = now
    logger.info("whisper: got {} words ({} segments)", len(words), seg_count)
    return words


def transcribe_words_many(
    audio_paths: list[Path],
    *,
    model_name: str = "medium",
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
) -> list[list[WordTS]]:
    """Whisper для нескольких файлов — модель грузится один раз."""
    if not audio_paths:
        return []
    if not whisper_available():
        raise ImportError(f"faster-whisper не установлен. {_WHISPER_INSTALL_HINT}")
    from faster_whisper import WhisperModel

    logger.info("whisper: loading model '{}' for {} clips ...", model_name, len(audio_paths))
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
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
        words: list[WordTS] = []
        for seg in segments:
            for w in seg.words or []:
                words.append(WordTS(
                    word=w.word.strip(),
                    start=float(w.start),
                    end=float(w.end),
                    prob=float(getattr(w, "probability", 0.0)),
                ))
        out.append(words)
    return out


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
