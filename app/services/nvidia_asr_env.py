"""Ранний redirect TEMP/HF cache для NeMo — до import huggingface/nemo."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from loguru import logger

_configured = False
_STALE_LOCK_MAX_AGE_S = 900.0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def clear_stale_nvidia_load_lock(cache_root: Path | None = None) -> bool:
    """Снять зависший parakeet.load.lock после краша Studio (без ручных действий)."""
    if cache_root is None:
        from app.settings import settings

        cache_root = settings.data_dir / ".cache"
    lock_file = cache_root / "locks" / "parakeet.load.lock"
    if not lock_file.is_file():
        return False
    try:
        stat = lock_file.stat()
        age_s = time.time() - stat.st_mtime
        pid: int | None = None
        try:
            raw = lock_file.read_text(encoding="utf-8").strip()
            if raw:
                pid = int(raw.split()[0])
        except (OSError, ValueError):
            pid = None
        stale = age_s > _STALE_LOCK_MAX_AGE_S or (pid is not None and not _pid_alive(pid))
        if not stale:
            return False
        lock_file.unlink(missing_ok=True)
        logger.warning(
            "nvidia_asr: снят устаревший lock (age={:.0f}s, pid={})",
            age_s,
            pid,
        )
        return True
    except OSError as exc:
        logger.warning("nvidia_asr: не удалось проверить lock {}: {}", lock_file, exc)
        return False


def clear_stale_nemo_temp_dirs(cache_root: Path | None = None) -> int:
    """Удалить старые tmp* от HF (manifest.json WinError 32 на Windows)."""
    import shutil

    if cache_root is None:
        from app.settings import settings

        cache_root = settings.data_dir / ".cache"
    temp_root = cache_root / "temp"
    if not temp_root.is_dir():
        return 0
    removed = 0
    now = time.time()
    for entry in temp_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith("tmp"):
            continue
        try:
            if now - entry.stat().st_mtime < 1800:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed += 1
        except OSError:
            continue
    if removed:
        logger.warning("nvidia_asr: удалено {} старых temp-папок HF", removed)
    return removed


def configure_nvidia_asr_environment(*, force: bool = True) -> Path:
    """Windows: NeMo/HF пишут manifest.json в %TEMP% → WinError 32.

    Перенаправляем TEMP/TMP и HF cache в data/.cache до загрузки модели.
    """
    global _configured
    from app.settings import settings

    cache_root = settings.data_dir / ".cache"
    hf_root = cache_root / "huggingface"
    hf_hub = hf_root / "hub"
    temp_root = cache_root / "temp"
    nemo_root = cache_root / "nemo"
    for path in (cache_root, hf_root, hf_hub, temp_root, nemo_root):
        path.mkdir(parents=True, exist_ok=True)

    temp_s = str(temp_root.resolve())
    hf_s = str(hf_root.resolve())
    hub_s = str(hf_hub.resolve())
    nemo_s = str(nemo_root.resolve())

    env_pairs = {
        "TEMP": temp_s,
        "TMP": temp_s,
        "TMPDIR": temp_s,
        "HF_HOME": hf_s,
        "HUGGINGFACE_HUB_CACHE": hub_s,
        "TRANSFORMERS_CACHE": hub_s,
        "NEMO_CACHE_DIR": nemo_s,
        "HF_HUB_DISABLE_SYMLINKS": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "0",
        "HF_HUB_ENABLE_HF_TRANSFER": "0",
        # Xet пишет manifest.json во temp → WinError 32 при параллельной загрузке на Windows
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DOWNLOAD_TIMEOUT": "600",
        "HF_HUB_ETAG_TIMEOUT": "60",
        "HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    for key, value in env_pairs.items():
        if force or key not in os.environ:
            os.environ[key] = value

    tempfile.tempdir = temp_s

    clear_stale_nvidia_load_lock(cache_root)
    clear_stale_nemo_temp_dirs(cache_root)

    if not _configured:
        logger.info(
            "nvidia_asr env: TEMP/TMP → {}; HF → {}",
            temp_root,
            hf_root,
        )
        _configured = True
    return cache_root
