"""Ранний redirect TEMP/HF cache для NeMo — до import huggingface/nemo."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from loguru import logger

_configured = False


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
        "TOKENIZERS_PARALLELISM": "false",
    }
    for key, value in env_pairs.items():
        if force or key not in os.environ:
            os.environ[key] = value

    tempfile.tempdir = temp_s

    if not _configured:
        logger.info(
            "nvidia_asr env: TEMP/TMP → {}; HF → {}",
            temp_root,
            hf_root,
        )
        _configured = True
    return cache_root
