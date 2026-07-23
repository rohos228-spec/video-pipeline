"""Самый ранний bootstrap: до loguru/settings/nemo/huggingface.

Windows + NVIDIA ASR: изолированный TEMP на процесс, отключение HF Xet.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_applied = False
_mkdtemp_patched = False


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    raw = (os.environ.get("DATA_DIR") or "./data").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _repo_root() / path
    return path.resolve()


def _patch_mkdtemp(pid_temp: str) -> None:
    global _mkdtemp_patched
    if _mkdtemp_patched:
        return
    original = tempfile.mkdtemp

    def mkdtemp(suffix=None, prefix=None, dir=None):  # noqa: ANN001
        return original(suffix=suffix, prefix=prefix or "t", dir=dir or pid_temp)

    tempfile.mkdtemp = mkdtemp  # type: ignore[assignment]
    _mkdtemp_patched = True


def apply_nvidia_env(*, force: bool = True) -> Path:
    """TEMP/TMP/HF env + temp на процесс (pid-*), чтобы не делить tmp*/manifest.json."""
    global _applied
    cache = _data_dir() / ".cache"
    temp_root = cache / "temp"
    pid_temp = temp_root / f"pid-{os.getpid()}"
    hf_root = cache / "huggingface"
    hf_hub = hf_root / "hub"
    nemo_root = cache / "nemo"
    for path in (cache, temp_root, pid_temp, hf_root, hf_hub, nemo_root):
        path.mkdir(parents=True, exist_ok=True)

    temp_s = str(pid_temp.resolve())
    pairs = {
        "TEMP": temp_s,
        "TMP": temp_s,
        "TMPDIR": temp_s,
        "HF_HOME": str(hf_root.resolve()),
        "HUGGINGFACE_HUB_CACHE": str(hf_hub.resolve()),
        "TRANSFORMERS_CACHE": str(hf_hub.resolve()),
        "NEMO_CACHE_DIR": str(nemo_root.resolve()),
        "HF_HUB_DISABLE_SYMLINKS": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "0",
        "HF_HUB_ENABLE_HF_TRANSFER": "0",
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DOWNLOAD_TIMEOUT": "600",
        "HF_HUB_ETAG_TIMEOUT": "60",
        "HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    for key, value in pairs.items():
        if force or key not in os.environ:
            os.environ[key] = value

    tempfile.tempdir = temp_s
    _patch_mkdtemp(temp_s)
    _applied = True
    return cache


def set_hf_offline(*, offline: bool = True) -> None:
    flag = "1" if offline else "0"
    os.environ["HF_HUB_OFFLINE"] = flag
    os.environ["TRANSFORMERS_OFFLINE"] = flag


def apply_if_nvidia_asr(*, force: bool = True) -> None:
    backend = (os.environ.get("ASR_BACKEND") or "nvidia").strip().lower()
    if backend == "nvidia":
        apply_nvidia_env(force=force)


# Авто-при import app.bootstrap_env (до остальных модулей app.*)
if sys.platform == "win32" or (os.environ.get("ASR_BACKEND") or "nvidia").strip().lower() == "nvidia":
    apply_if_nvidia_asr(force=True)
