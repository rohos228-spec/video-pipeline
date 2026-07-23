"""Самый ранний bootstrap: до loguru/settings/nemo/huggingface."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_applied = False
_mkdtemp_patched = False
_tempdir_patched = False


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


def _patch_tempdir_cleanup_win32() -> None:
    """NeMo transcribe() падает → cleanup temp/manifest.json → WinError 32 на Windows."""
    global _tempdir_patched
    if _tempdir_patched or sys.platform != "win32":
        return
    original_cleanup = tempfile.TemporaryDirectory.cleanup

    def safe_cleanup(self) -> None:  # noqa: ANN001
        try:
            original_cleanup(self)
        except PermissionError:
            pass
        except OSError as exc:
            if getattr(exc, "winerror", None) == 32:
                pass
            else:
                raise

    tempfile.TemporaryDirectory.cleanup = safe_cleanup  # type: ignore[method-assign]
    _tempdir_patched = True


def apply_nvidia_env(*, force: bool = True) -> Path:
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
    _patch_tempdir_cleanup_win32()
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


if sys.platform == "win32" or (os.environ.get("ASR_BACKEND") or "nvidia").strip().lower() == "nvidia":
    apply_if_nvidia_asr(force=True)
