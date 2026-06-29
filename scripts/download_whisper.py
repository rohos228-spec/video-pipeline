"""Скачать ASR-модель заранее (Whisper или NVIDIA Parakeet).

  python scripts/download_whisper.py
  python scripts/download_whisper.py large-v3
  ASR_BACKEND=nvidia python scripts/download_whisper.py
"""

from __future__ import annotations

import sys

from app.settings import settings


def main() -> int:
    backend = (settings.asr_backend or "whisper").strip().lower()
    if backend == "nvidia":
        from app.services.asr.nvidia_backend import _load_model

        name = sys.argv[1] if len(sys.argv) > 1 else settings.nvidia_asr_model
        print(f"Downloading / loading NVIDIA ASR '{name}' …")
        _load_model(name)
        print(f"OK: '{name}' ready")
        return 0

    from app.services.asr.whisper_backend import _create_model

    name = sys.argv[1] if len(sys.argv) > 1 else settings.whisper_model
    print(f"Downloading / loading Whisper '{name}' …")
    _create_model(name, settings.whisper_device, settings.whisper_compute_type)
    print(f"OK: '{name}' ready in HF cache")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
