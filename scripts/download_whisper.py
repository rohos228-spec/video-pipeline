"""Скачать модель faster-whisper заранее (чтобы сборка не зависала на HF).

  python scripts/download_whisper.py
  python scripts/download_whisper.py large-v3
"""

from __future__ import annotations

import sys

from app.services.whisper import _create_model
from app.settings import settings


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else settings.whisper_model
    print(f"Downloading / loading Whisper '{name}' …")
    _create_model(name, settings.whisper_device, settings.whisper_compute_type)
    print(f"OK: '{name}' ready in HF cache")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
