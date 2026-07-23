"""NVIDIA NeMo Parakeet — word-level ASR (ASR_BACKEND=nvidia)."""

from __future__ import annotations

import threading
from pathlib import Path

from loguru import logger

from app.services.whisper import WordTS

_model_lock = threading.Lock()
_model_cache: dict[str, object] = {}

_NVIDIA_INSTALL_HINT = (
    'pip install -e ".[nvidia]"   # NeMo + Parakeet на ПК монтажа (CUDA)'
)


def nvidia_asr_available() -> bool:
    try:
        import nemo.collections.asr  # noqa: F401
        return True
    except ImportError:
        return False


def _load_model(model_name: str):
    if not nvidia_asr_available():
        raise ImportError(f"NeMo ASR не установлен. {_NVIDIA_INSTALL_HINT}")
    import nemo.collections.asr as nemo_asr

    with _model_lock:
        cached = _model_cache.get(model_name)
        if cached is not None:
            return cached
        logger.info("nvidia_asr: loading model '{}' …", model_name)
        model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
        _model_cache[model_name] = model
        return model


def _word_stamp_seconds(stamp: dict, model) -> tuple[float, float, str]:
    text = str(stamp.get("word") or stamp.get("char") or "").strip()
    if "start" in stamp and "end" in stamp:
        return float(stamp["start"]), float(stamp["end"]), text
    stride = 0.01
    try:
        stride = float(model.cfg.preprocessor.get("window_stride", stride))
    except Exception:  # noqa: BLE001
        pass
    start = float(stamp.get("start_offset", stamp.get("start", 0.0))) * stride
    end = float(stamp.get("end_offset", stamp.get("end", start))) * stride
    if "start" in stamp and "start_offset" not in stamp:
        start = float(stamp["start"])
    if "end" in stamp and "end_offset" not in stamp:
        end = float(stamp["end"])
    return start, end, text


def _hypothesis_words(hypothesis, model) -> list[WordTS]:
    ts = getattr(hypothesis, "timestamp", None) or {}
    if not isinstance(ts, dict):
        return []
    raw_words = ts.get("word") or []
    out: list[WordTS] = []
    for stamp in raw_words:
        if not isinstance(stamp, dict):
            continue
        start, end, text = _word_stamp_seconds(stamp, model)
        if not text:
            continue
        if end < start:
            end = start
        out.append(WordTS(word=text, start=round(start, 3), end=round(end, 3), prob=1.0))
    return out


def transcribe_words_nvidia(
    audio_path: Path,
    *,
    model_name: str,
    language: str = "ru",
) -> list[WordTS]:
    """Word-level таймкоды через NVIDIA NeMo Parakeet."""
    import time

    if not audio_path.is_file():
        raise FileNotFoundError(f"audio not found: {audio_path}")

    model = _load_model(model_name)
    logger.info(
        "nvidia_asr: transcribing {} (model={}, lang={})",
        audio_path.name,
        model_name,
        language,
    )
    t0 = time.monotonic()
    hypotheses = model.transcribe(
        [str(audio_path.resolve())],
        timestamps=True,
        verbose=False,
    )
    if not hypotheses:
        logger.warning("nvidia_asr: empty hypotheses for {}", audio_path.name)
        return []

    hyp = hypotheses[0]
    words = _hypothesis_words(hyp, model)
    elapsed = time.monotonic() - t0
    text_preview = (getattr(hyp, "text", "") or "")[:80]
    logger.info(
        "nvidia_asr: {} words in {:.1f}s — «{}…»",
        len(words),
        elapsed,
        text_preview,
    )
    return words


def transcribe_words_many_nvidia(
    audio_paths: list[Path],
    *,
    model_name: str,
    language: str = "ru",
) -> list[list[WordTS]]:
    if not audio_paths:
        return []
    model = _load_model(model_name)
    paths = [str(p.resolve()) for p in audio_paths if p.is_file()]
    logger.info("nvidia_asr: batch transcribe {} files", len(paths))
    hypotheses = model.transcribe(paths, timestamps=True, verbose=False)
    out: list[list[WordTS]] = []
    for hyp in hypotheses:
        out.append(_hypothesis_words(hyp, model))
    return out
