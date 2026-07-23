"""NVIDIA NeMo Parakeet — word-level ASR (ASR_BACKEND=nvidia)."""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
import time
from pathlib import Path

from loguru import logger

from app.bootstrap_env import apply_nvidia_env, set_hf_offline
from app.services.nvidia_asr_env import configure_nvidia_asr_environment
from app.services.whisper import WordTS

apply_nvidia_env(force=True)
configure_nvidia_asr_environment(force=True)

_model_lock = threading.Lock()
_transcribe_lock = threading.Lock()
_model_cache: dict[str, object] = {}

_PARAKEET_V3 = "nvidia/parakeet-tdt-0.6b-v3"
_LEGACY_NVIDIA_MARKERS = ("fastconformer", "stt_ru_", "conformer_hybrid")

_NEMO_FILENAME_BY_REPO: dict[str, str] = {
    _PARAKEET_V3: "parakeet-tdt-0.6b-v3.nemo",
    "nvidia/parakeet-tdt-0.6b-v2": "parakeet-tdt-0.6b-v2.nemo",
}
_MIN_NEMO_BYTES = 50_000_000

_NVIDIA_INSTALL_HINT = (
    'pip install -e ".[nvidia]"   # NeMo + Parakeet на ПК монтажа (CUDA)'
)
_LOAD_RETRIES = 8
_LOAD_RETRY_SLEEP_S = 4.0
_LOAD_LOCK_TIMEOUT_S = 900.0


def normalize_nvidia_asr_model(model_name: str) -> str:
    """Word-level монтаж — только Parakeet; FastConformer из старого .env игнорируем."""
    name = (model_name or "").strip()
    if not name:
        return _PARAKEET_V3
    lower = name.lower()
    if name.startswith("nvidia/") and "parakeet" in lower:
        return name
    if any(marker in lower for marker in _LEGACY_NVIDIA_MARKERS):
        logger.warning(
            "nvidia_asr: {} устарел для word-level монтажа — используем {}",
            name,
            _PARAKEET_V3,
        )
        return _PARAKEET_V3
    return name


def nvidia_asr_available() -> bool:
    """Проверка NeMo без import nemo (import тянет transformers → HF temp manifest)."""
    import importlib.util

    configure_nvidia_asr_environment(force=True)
    return importlib.util.find_spec("nemo.collections.asr") is not None


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


def _nemo_filename(model_name: str) -> str:
    known = _NEMO_FILENAME_BY_REPO.get(model_name.strip())
    if known:
        return known
    tail = model_name.rsplit("/", 1)[-1]
    return tail if tail.endswith(".nemo") else f"{tail}.nemo"


def _stable_nemo_path(model_name: str, cache_dir: Path) -> Path:
    slug = model_name.replace("/", "--")
    return cache_dir / "nemo" / f"{slug}.nemo"


def _nemo_file_ready(path: Path | None) -> bool:
    if path is None or not path.is_file():
        return False
    try:
        return path.stat().st_size >= _MIN_NEMO_BYTES
    except OSError:
        return False


def _with_interprocess_load_lock(cache_dir: Path, model_name: str) -> Path | None:
    """Один процесс качает модель — остальные ждут или используют готовый .nemo."""
    from app.services.nvidia_asr_env import clear_stale_nvidia_load_lock

    lock_dir = cache_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "parakeet.load.lock"
    deadline = time.monotonic() + _LOAD_LOCK_TIMEOUT_S
    while time.monotonic() < deadline:
        clear_stale_nvidia_load_lock(cache_dir)
        if _nemo_file_ready(_find_local_nemo_checkpoint(model_name, cache_dir)):
            return None
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.close(fd)
            return lock_file
        except FileExistsError:
            waited = _LOAD_LOCK_TIMEOUT_S - (deadline - time.monotonic())
            if int(waited) % 15 < 2:
                part = _stable_nemo_path(model_name, cache_dir).with_suffix(".nemo.part")
                mb = part.stat().st_size / 1_000_000 if part.is_file() else 0.0
                logger.info(
                    "nvidia_asr: ждём скачивание Parakeet (~2.5 GB) — {:.0f}s, {:.0f} MB …",
                    waited,
                    mb,
                )
            time.sleep(2.0)
    raise TimeoutError(
        "nvidia_asr: другой процесс загружает Parakeet — таймаут ожидания. "
        "Закройте все окна Studio/run-backend и перезапустите STUDIO.cmd."
    )


def _release_interprocess_load_lock(lock_file: Path | None) -> None:
    if lock_file is None:
        return
    try:
        lock_file.unlink(missing_ok=True)
    except OSError as exc:
        if not _is_file_lock_error(exc):
            logger.warning("nvidia_asr: не удалось снять lock {}: {}", lock_file, exc)


def _find_local_nemo_checkpoint(model_name: str, cache_dir: Path) -> Path | None:
    """Только стабильный data/.cache/nemo/*.nemo — без HF hub cache."""
    stable = _stable_nemo_path(model_name, cache_dir)
    if _nemo_file_ready(stable):
        return stable
    slug = model_name.replace("/", "--")
    legacy = cache_dir / "nemo" / f"{slug}.nemo"
    if legacy.is_file() and legacy.resolve() != stable.resolve() and _nemo_file_ready(legacy):
        return legacy
    return None


def _ensure_nemo_on_disk(model_name: str, cache_dir: Path) -> Path:
    """Скачать .nemo по HTTP до любого import nemo/huggingface."""
    local = _find_local_nemo_checkpoint(model_name, cache_dir)
    if local is not None:
        return local
    return _http_download_nemo(model_name, cache_dir)


def _ensure_stable_nemo_copy(model_name: str, cache_dir: Path, source: Path) -> Path:
    dest = _stable_nemo_path(model_name, cache_dir)
    if _nemo_file_ready(dest):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    if part.exists():
        with contextlib.suppress(OSError):
            part.unlink()
    shutil.copy2(source, part)
    part.replace(dest)
    return dest


def _http_download_nemo(model_name: str, cache_dir: Path) -> Path:
    """Скачать .nemo напрямую по HTTP — без huggingface_hub (нет manifest.json в temp)."""
    stable = _stable_nemo_path(model_name, cache_dir)
    if _nemo_file_ready(stable):
        return stable

    import httpx

    filename = _nemo_filename(model_name)
    url = f"https://huggingface.co/{model_name}/resolve/main/{filename}"
    part = stable.with_suffix(stable.suffix + ".part")
    stable.parent.mkdir(parents=True, exist_ok=True)

    resume_from = part.stat().st_size if part.is_file() else 0
    headers: dict[str, str] = {}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"

    logger.info(
        "nvidia_asr: HTTP download {} → {} (resume {:.1f} MB)",
        url,
        stable.name,
        resume_from / 1_000_000,
    )

    last_exc: BaseException | None = None
    for attempt in range(1, _LOAD_RETRIES + 1):
        try:
            timeout = httpx.Timeout(600.0, connect=60.0)
            with httpx.stream(
                "GET", url, headers=headers, follow_redirects=True, timeout=timeout,
            ) as resp:
                if resp.status_code == 416:
                    if _nemo_file_ready(part):
                        part.replace(stable)
                        return stable
                    part.unlink(missing_ok=True)
                    headers.pop("Range", None)
                    resume_from = 0
                    continue
                resp.raise_for_status()
                mode = "ab" if resume_from > 0 and resp.status_code == 206 else "wb"
                if mode == "wb" and part.exists():
                    part.unlink(missing_ok=True)
                downloaded = resume_from
                last_log = time.monotonic()
                with part.open(mode) as out:
                    for chunk in resp.iter_bytes(1024 * 1024):
                        if chunk:
                            out.write(chunk)
                            downloaded += len(chunk)
                            now = time.monotonic()
                            if now - last_log >= 15.0:
                                logger.info(
                                    "nvidia_asr: скачано {:.0f} MB …",
                                    downloaded / 1_000_000,
                                )
                                last_log = now
            if not _nemo_file_ready(part):
                size = part.stat().st_size if part.is_file() else 0
                raise RuntimeError(f"nvidia_asr: неполная загрузка .nemo ({size} bytes)")
            part.replace(stable)
            logger.info(
                "nvidia_asr: downloaded {} ({:.2f} GB)",
                stable.name,
                stable.stat().st_size / 1_000_000_000,
            )
            return stable
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            resume_from = part.stat().st_size if part.is_file() else 0
            if resume_from > 0:
                headers["Range"] = f"bytes={resume_from}-"
            else:
                headers.pop("Range", None)
            if not _is_file_lock_error(exc) or attempt >= _LOAD_RETRIES:
                raise
            wait = _LOAD_RETRY_SLEEP_S * attempt
            logger.warning(
                "nvidia_asr: WinError 32 при HTTP-скачивании (попытка {}/{}), "
                "повтор через {:.0f}s: {}",
                attempt,
                _LOAD_RETRIES,
                wait,
                exc,
            )
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"nvidia_asr: не удалось скачать {model_name}")


def _restore_nemo_model(model_name: str, nemo_path: Path):
    set_hf_offline(offline=True)
    configure_nvidia_asr_environment(force=True)
    import nemo.collections.asr as nemo_asr

    logger.info("nvidia_asr: restore_from {}", nemo_path)
    return nemo_asr.models.ASRModel.restore_from(restore_path=str(nemo_path.resolve()))


def _download_model(model_name: str):
    cache_dir = _cache_root()
    configure_nvidia_asr_environment(force=True)
    nemo_path = _ensure_nemo_on_disk(model_name, cache_dir)
    return _restore_nemo_model(model_name, nemo_path)


def _load_model(model_name: str):
    if not nvidia_asr_available():
        raise ImportError(f"NeMo ASR не установлен. {_NVIDIA_INSTALL_HINT}")

    model_name = normalize_nvidia_asr_model(model_name)

    with _model_lock:
        cached = _model_cache.get(model_name)
        if cached is not None:
            return cached

    cache_dir = _cache_root()
    nemo_path = _find_local_nemo_checkpoint(model_name, cache_dir)
    if nemo_path is None:
        logger.info("nvidia_asr: loading model '{}' …", model_name)
        lock_file: Path | None = None
        last_exc: BaseException | None = None
        try:
            lock_file = _with_interprocess_load_lock(cache_dir, model_name)
            for attempt in range(1, _LOAD_RETRIES + 1):
                try:
                    nemo_path = _ensure_nemo_on_disk(model_name, cache_dir)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if not _is_file_lock_error(exc) or attempt >= _LOAD_RETRIES:
                        raise
                    wait = _LOAD_RETRY_SLEEP_S * attempt
                    logger.warning(
                        "nvidia_asr: WinError 32 при скачивании (попытка {}/{}), "
                        "повтор через {:.0f}s: {}",
                        attempt,
                        _LOAD_RETRIES,
                        wait,
                        exc,
                    )
                    time.sleep(wait)
            else:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(f"nvidia_asr: не удалось скачать {model_name}")
        finally:
            _release_interprocess_load_lock(lock_file)
    else:
        logger.info("nvidia_asr: restore model '{}' from disk …", model_name)

    with _model_lock:
        cached = _model_cache.get(model_name)
        if cached is not None:
            return cached
        model = _restore_nemo_model(model_name, nemo_path)
        _model_cache[model_name] = model
        logger.info("nvidia_asr: model '{}' ready", model_name)
        return model


def ensure_nvidia_asr_ready(model_name: str | None = None) -> None:
    """Дождаться скачивания и restore Parakeet — перед transcribe."""
    from app.settings import settings

    name = normalize_nvidia_asr_model(model_name or settings.nvidia_asr_model)
    _load_model(name)


def preload_nvidia_asr_model(model_name: str | None = None) -> bool:
    """Предзагрузка Parakeet (фон при старте Studio или шаг «Аудио»)."""
    from app.settings import settings

    configure_nvidia_asr_environment(force=True)
    name = normalize_nvidia_asr_model(model_name or settings.nvidia_asr_model)
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


def _probe_audio_channels(path: Path) -> int:
    import subprocess

    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=channels", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 1
    try:
        return max(1, int(proc.stdout.strip()))
    except ValueError:
        return 1


def _ensure_mono_for_nemo(audio_path: Path) -> Path:
    """NeMo ждёт mono (batch, time). Стерео voice_full → TypeError + WinError 32 на cleanup."""
    channels = _probe_audio_channels(audio_path)
    if channels <= 1:
        return audio_path
    mono_dir = _cache_root() / "mono"
    mono_dir.mkdir(parents=True, exist_ok=True)
    out = mono_dir / f"{audio_path.stem}_mono16k.wav"
    src_mtime = audio_path.stat().st_mtime
    if out.is_file() and out.stat().st_mtime >= src_mtime and out.stat().st_size > 1000:
        logger.info("nvidia_asr: используем mono-кэш {}", out.name)
        return out
    import subprocess

    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path.resolve()),
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(out.resolve()),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"nvidia_asr: ffmpeg stereo→mono failed for {audio_path.name}: {proc.stderr}"
        )
    logger.warning(
        "nvidia_asr: {} — {} канал(ов), конвертировано в mono {}",
        audio_path.name,
        channels,
        out.name,
    )
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

    resolved_model = normalize_nvidia_asr_model(model_name)
    model = _load_model(resolved_model)
    mono_path = _ensure_mono_for_nemo(audio_path)
    logger.info(
        "nvidia_asr: transcribing {} (model={}, lang={})",
        mono_path.name,
        resolved_model,
        language,
    )
    t0 = time.monotonic()
    stop_heartbeat = threading.Event()

    def _transcribe_heartbeat() -> None:
        while not stop_heartbeat.wait(30.0):
            logger.info(
                "nvidia_asr: transcribe ещё идёт ({:.0f}s) — {} …",
                time.monotonic() - t0,
                mono_path.name,
            )

    heartbeat = threading.Thread(target=_transcribe_heartbeat, daemon=True)
    heartbeat.start()
    try:
        with _transcribe_lock:
            hypotheses = model.transcribe(
                [str(mono_path.resolve())],
                timestamps=True,
                verbose=False,
            )
    finally:
        stop_heartbeat.set()
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
    with _transcribe_lock:
        paths = [str(_ensure_mono_for_nemo(p).resolve()) for p in audio_paths if p.is_file()]
        logger.info("nvidia_asr: batch transcribe {} files", len(paths))
        hypotheses = model.transcribe(paths, timestamps=True, verbose=False)
    out: list[list[WordTS]] = []
    for hyp in hypotheses:
        out.append(_hypothesis_words(hyp, model))
    return out
