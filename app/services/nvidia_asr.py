"""NVIDIA NeMo Parakeet — word-level ASR (ASR_BACKEND=nvidia)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from loguru import logger

from app.services.nvidia_asr_env import configure_nvidia_asr_environment
from app.services.whisper import WordTS

# До любого import nemo/huggingface — иначе останется %TEMP%.
configure_nvidia_asr_environment(force=True)

_model_lock = threading.Lock()
_model_cache: dict[str, object] = {}

_NVIDIA_INSTALL_HINT = (
    'pip install -e ".[nvidia]"   # NeMo + Parakeet на ПК монтажа (CUDA)'
)
_LOAD_RETRIES = 8
_LOAD_RETRY_SLEEP_S = 4.0
_LOAD_LOCK_TIMEOUT_S = 900.0


def nvidia_asr_available() -> bool:
    configure_nvidia_asr_environment(force=True)
    try:
        import nemo.collections.asr  # noqa: F401
        return True
    except ImportError:
        return False


def _cache_root() -> Path:
    return configure_nvidia_asr_environment(force=True)


def _is_file_lock_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 32:
        return True
    text = str(exc).lower()
    return (
        "winerror 32" in text
        or "used by another process" in text
        or "занят другим процессом" in text
    )


def _with_interprocess_load_lock(cache_dir: Path) -> Path:
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
            time.sleep(2.0)
    raise TimeoutError(
        "nvidia_asr: другой процесс загружает Parakeet — таймаут ожидания. "
        "Закройте все Studio/python, удалите data/.cache/huggingface/locks/ "
        "и запустите: python scripts/download_nvidia_asr.py"
    )


def _release_interprocess_load_lock(lock_file: Path | None) -> None:
    if lock_file is None:
        return
    try:
        lock_file.unlink(missing_ok=True)
    except OSError as exc:
        if not _is_file_lock_error(exc):
            logger.warning("nvidia_asr: не удалось снять lock {}: {}", lock_file, exc)


def _hf_cache_slug(model_name: str) -> str:
    return "models--" + model_name.replace("/", "--")


def _find_local_nemo_checkpoint(model_name: str, cache_dir: Path) -> Path | None:
    """Ищем уже скачанный .nemo — restore_from без temp manifest."""
    slug = model_name.replace("/", "--")
    nemo_dest = cache_dir / "nemo" / f"{slug}.nemo"
    if nemo_dest.is_file():
        return nemo_dest
    hub = cache_dir / "huggingface" / "hub"
    hf_dir = hub / _hf_cache_slug(model_name)
    if hf_dir.is_dir():
        for nemo in hf_dir.rglob("*.nemo"):
            if nemo.is_file():
                return nemo
    return None


def _snapshot_download_model(model_name: str, cache_dir: Path) -> Path | None:
    """Скачать HF repo в hub cache; вернуть .nemo если есть."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        logger.warning("huggingface_hub не установлен — from_pretrained напрямую")
        return None

    hub = cache_dir / "huggingface" / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    configure_nvidia_asr_environment(force=True)
    local_dir = snapshot_download(
        repo_id=model_name,
        cache_dir=str(hub),
        resume_download=True,
    )
    root = Path(local_dir)
    for nemo in root.rglob("*.nemo"):
        if nemo.is_file():
            return nemo
    return None


def _download_model(model_name: str):
    import nemo.collections.asr as nemo_asr

    cache_dir = _cache_root()
    configure_nvidia_asr_environment(force=True)

    local = _find_local_nemo_checkpoint(model_name, cache_dir)
    if local is not None:
        logger.info("nvidia_asr: restore_from local {}", local)
        return nemo_asr.models.ASRModel.restore_from(restore_path=str(local))

    try:
        nemo_path = _snapshot_download_model(model_name, cache_dir)
        if nemo_path is not None:
            dest = cache_dir / "nemo" / f"{model_name.replace('/', '--')}.nemo"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_file():
                dest.write_bytes(nemo_path.read_bytes())
            logger.info("nvidia_asr: restore_from snapshot {}", dest)
            return nemo_asr.models.ASRModel.restore_from(restore_path=str(dest))
    except Exception as exc:  # noqa: BLE001
        if not _is_file_lock_error(exc):
            logger.warning("nvidia_asr: snapshot_download failed: {}", exc)

    logger.info("nvidia_asr: from_pretrained fallback {}", model_name)
    return nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)


def _load_model(model_name: str):
    if not nvidia_asr_available():
        raise ImportError(f"NeMo ASR не установлен. {_NVIDIA_INSTALL_HINT}")

    with _model_lock:
        cached = _model_cache.get(model_name)
        if cached is not None:
            return cached

        cache_dir = _cache_root()
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
                    wait = _LOAD_RETRY_SLEEP_S * attempt
                    logger.warning(
                        "nvidia_asr: WinError 32 (попытка {}/{}), повтор через {:.0f}s: {}",
                        attempt,
                        _LOAD_RETRIES,
                        wait,
                        exc,
                    )
                    time.sleep(wait)
        finally:
            _release_interprocess_load_lock(lock_file)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"nvidia_asr: не удалось загрузить {model_name}")


def preload_nvidia_asr_model(model_name: str | None = None) -> bool:
    """Явная предзагрузка Parakeet (скрипт / первый запуск Studio)."""
    from app.settings import settings

    configure_nvidia_asr_environment(force=True)
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
