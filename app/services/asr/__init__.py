"""ASR backends: faster-whisper (CPU/CUDA) и NVIDIA NeMo Parakeet."""

from app.services.asr.engine import (
    asr_available,
    get_asr_backend_label,
    transcribe_words,
    transcribe_words_many,
)

__all__ = [
    "asr_available",
    "get_asr_backend_label",
    "transcribe_words",
    "transcribe_words_many",
]
