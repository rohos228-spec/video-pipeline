"""Legacy FastConformer .env — не перекачивать, если .nemo уже на диске."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import nvidia_asr
from app.services.nvidia_asr import _MIN_NEMO_BYTES, normalize_nvidia_asr_model

_FAST = "nvidia/stt_ru_fastconformer_hybrid_large_pc"
_PARAKEET = "nvidia/parakeet-tdt-0.6b-v3"


def test_normalize_keeps_parakeet_v3() -> None:
    assert normalize_nvidia_asr_model(_PARAKEET) == _PARAKEET


def test_normalize_empty_defaults_to_parakeet() -> None:
    assert normalize_nvidia_asr_model("") == _PARAKEET


def test_normalize_keeps_fastconformer_when_on_disk(tmp_path: Path) -> None:
    local = tmp_path / "nvidia--stt_ru_fastconformer_hybrid_large_pc.nemo"
    local.write_bytes(b"x" * (_MIN_NEMO_BYTES + 1))
    with patch.object(nvidia_asr, "_find_local_nemo_checkpoint", return_value=local):
        assert normalize_nvidia_asr_model(_FAST) == _FAST


def test_normalize_redirects_fastconformer_when_missing() -> None:
    with patch.object(nvidia_asr, "_find_local_nemo_checkpoint", return_value=None):
        assert normalize_nvidia_asr_model(_FAST) == _PARAKEET
