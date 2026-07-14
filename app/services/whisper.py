"""faster-whisper: транскрибация озвучки в список слов с таймкодами."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

# Windows: WinError 1314 при symlink в HF cache — копировать файлы вместо ссылок.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "0")


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


def _resolve_whisper_runtime(
    device: str | None = None,
    compute_type: str | None = None,
) -> tuple[str, str]:
    from app.settings import settings

    dev = device or settings.whisper_device
    ctype = compute_type or settings.whisper_compute_type
    if dev != "cuda":
        return dev, ctype
    try:
        import torch

        if torch.cuda.is_available():
            return dev, ctype
    except ImportError:
        pass
    logger.warning(
        "whisper: CUDA недоступна — fallback device=cpu compute_type=int8"
    )
    return "cpu", "int8"


def _create_model(
    model_name: str,
    device: str | None = None,
    compute_type: str | None = None,
):
    if not whisper_available():
        raise ImportError(f"faster-whisper не установлен. {_WHISPER_INSTALL_HINT}")
    from faster_whisper import WhisperModel

    dev, ctype = _resolve_whisper_runtime(device, compute_type)
    logger.info(
        "whisper: loading model '{}' (device={}, compute={}) ...",
        model_name,
        dev,
        ctype,
    )
    return WhisperModel(model_name, device=dev, compute_type=ctype)


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
    """Word-level таймкоды; vad_filter=False — сохраняет паузы между словами."""
    if not whisper_available():
        raise ImportError(f"faster-whisper не установлен. {_WHISPER_INSTALL_HINT}")
    import time

    model = _create_model(model_name, device, compute_type)
    dev, _ = _resolve_whisper_runtime(device, compute_type)
    logger.info(
        "whisper: transcribing {} (vad_filter={}, beam={}, device={}) — "
        "на CPU это может занять несколько минут для длинного файла",
        audio_path.name,
        vad_filter,
        beam_size,
        dev,
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
        if now - last_log >= 15.0:
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
    device: str | None = None,
    compute_type: str | None = None,
) -> list[list[WordTS]]:
    """Whisper для нескольких файлов — модель грузится один раз."""
    if not audio_paths:
        return []
    if not whisper_available():
        raise ImportError(f"faster-whisper не установлен. {_WHISPER_INSTALL_HINT}")

    model = _create_model(model_name, device, compute_type)
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


def artifact_path_mtime(artifact) -> float | None:
    if artifact is None or not getattr(artifact, "path", None):
        return None
    path = Path(artifact.path)
    if not path.is_file():
        return None
    return path.stat().st_mtime


def whisper_words_fresh_for_audio(whisper_art, audio_path: Path) -> bool:
    """True если words.json новее или совпадает по времени с voice_full."""
    whisper_mtime = artifact_path_mtime(whisper_art)
    if whisper_mtime is None:
        return False
    if not audio_path.is_file():
        return True
    return whisper_mtime >= audio_path.stat().st_mtime


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
