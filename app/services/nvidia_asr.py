"""NVIDIA NeMo Parakeet — word-level ASR (ASR_BACKEND=nvidia)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from loguru import logger

from app.services.whisper import WordTS

# Windows: WinError 1314 / HF temp locks — кэш в data/, без symlink.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "0")

_model_lock = threading.Lock()
_model_cache: dict[str, object] = {}
_hf_cache_configured = False

_NVIDIA_INSTALL_HINT = (
    'pip install -e ".[nvidia]"   # NeMo + Parakeet на ПК монтажа (CUDA)'
)
_LOAD_RETRIES = 5
_LOAD_RETRY_SLEEP_S = 3.0
_LOAD_LOCK_TIMEOUT_S = 600.0


def nvidia_asr_available() -> bool:
    try:
        import nemo.collections.asr  # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_hf_cache_dir() -> Path:
    """Стабильный HF-кэш в data/ — меньше WinError 32 в %TEMP%."""
    global _hf_cache_configured
    from app.settings import settings

    cache_root = settings.data_dir / ".cache" / "huggingface"
    cache_root.mkdir(parents=True, exist_ok=True)
    hub = cache_root / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_root))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hub))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(hub))
    if not _hf_cache_configured:
        logger.info("nvidia_asr: HF cache → {}", cache_root)
        _hf_cache_configured = True
    return cache_root


def _is_file_lock_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 32:
        return True
    text = str(exc).lower()
    return "winerror 32" in text or "used by another process" in text


def _with_interprocess_load_lock(cache_dir: Path):
    """Один процесс качает модель — остальные ждут (Studio + worker)."""
    lock_dir = cache_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "parakeet.load.lock"
    deadline = time.monotonic() + _LOAD_LOCK_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock_file
        except FileExistsError:
            time.sleep(1.5)
    raise TimeoutError(
        "nvidia_asr: другой процесс загружает Parakeet — таймаут ожидания. "
        "Закройте лишние Studio/воркеры и повторите."
    )


def _release_interprocess_load_lock(lock_file: Path | None) -> None:
    if lock_file is None:
        return
    try:
        lock_file.unlink(missing_ok=True)
    except OSError as exc:
        if not _is_file_lock_error(exc):
            logger.warning("nvidia_asr: не удалось снять lock {}: {}", lock_file, exc)


def _download_model(model_name: str):
    import nemo.collections.asr as nemo_asr

    return nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)


def _load_model(model_name: str):
    if not nvidia_asr_available():
        raise ImportError(f"NeMo ASR не установлен. {_NVIDIA_INSTALL_HINT}")

    with _model_lock:
        cached = _model_cache.get(model_name)
        if cached is not None:
            return cached

        cache_dir = _ensure_hf_cache_dir()
        logger.info("nvidia_asr: loading model '{}' …", model_name)

        lock_file: Path | None = None
        last_exc: BaseException | None = None
        try:
            lock_file = _with_interprocess_load_lock(cache_dir)
            for attempt in range(1, _LOAD_RETRIES + 1):
                try:
                    model = _download_model(model_name)
                    _model_cache[model_name] = model
                    logger.info("nvidia_asr: model '{}' ready", model_name)
                    return model
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if not _is_file_lock_error(exc) or attempt >= _LOAD_RETRIES:
                        raise
                    logger.warning(
                        "nvidia_asr: WinError 32 при загрузке (попытка {}/{}), "
                        "повтор через {:.0f}s: {}",
                        attempt,
                        _LOAD_RETRIES,
                        _LOAD_RETRY_SLEEP_S,
                        exc,
                    )
                    time.sleep(_LOAD_RETRY_SLEEP_S * attempt)
        finally:
            _release_interprocess_load_lock(lock_file)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"nvidia_asr: не удалось загрузить {model_name}")


def preload_nvidia_asr_model(model_name: str | None = None) -> bool:
    """Явная предзагрузка Parakeet (скрипт / первый запуск Studio)."""
    from app.settings import settings

    name = (model_name or settings.nvidia_asr_model).strip()
    try:
        _load_model(name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("nvidia_asr preload failed: {}", exc)
        return False


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
