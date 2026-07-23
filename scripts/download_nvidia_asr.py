#!/usr/bin/env python3
"""Предзагрузка NVIDIA Parakeet в data/.cache/huggingface (обход WinError 32)."""

from __future__ import annotations

import sys

from loguru import logger


def main() -> int:
    from app.services.asr import active_asr_backend
    from app.services.nvidia_asr import preload_nvidia_asr_model

    if active_asr_backend() != "nvidia":
        logger.warning("ASR_BACKEND не nvidia — всё равно качаем Parakeet для монтажа")
    ok = preload_nvidia_asr_model()
    if ok:
        logger.info("Parakeet готов в data/.cache/huggingface")
        return 0
    logger.error("Не удалось загрузить Parakeet — закройте лишние Studio и повторите")
    return 1


if __name__ == "__main__":
    sys.exit(main())
