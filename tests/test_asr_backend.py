"""ASR backend routing: NVIDIA vs whisper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.asr import active_asr_backend, transcribe_words
from app.services.whisper import WordTS
from app.settings import settings


def test_active_asr_backend_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "asr_backend", "nvidia")
    assert active_asr_backend() == "nvidia"
    monkeypatch.setattr(settings, "asr_backend", "whisper")
    assert active_asr_backend() == "whisper"


def test_transcribe_words_uses_nvidia_when_configured(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "asr_backend", "nvidia")
    monkeypatch.setattr(settings, "nvidia_asr_model", "nvidia/parakeet-tdt-0.6b-v3")
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF")

    nvidia_words = [WordTS("привет", 0.0, 0.5, 1.0)]

    try:
        with (
            patch("app.services.nvidia_asr.nvidia_asr_available", return_value=True),
            patch(
                "app.services.nvidia_asr.transcribe_words_nvidia",
                return_value=nvidia_words,
            ) as mock_nvidia,
            patch("app.services.whisper.transcribe_words_whisper") as mock_whisper,
        ):
            out = transcribe_words(audio, language="ru")

        assert out == nvidia_words
        mock_nvidia.assert_called_once()
        mock_whisper.assert_not_called()
    finally:
        monkeypatch.undo()


def test_transcribe_words_ignores_whisper_model_name_for_nvidia(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "asr_backend", "nvidia")
    monkeypatch.setattr(settings, "nvidia_asr_model", "nvidia/parakeet-tdt-0.6b-v3")
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF")
    nvidia_words = [WordTS("тест", 0.0, 1.0, 1.0)]

    try:
        with (
            patch("app.services.nvidia_asr.nvidia_asr_available", return_value=True),
            patch(
                "app.services.nvidia_asr.transcribe_words_nvidia",
                return_value=nvidia_words,
            ) as mock_nvidia,
        ):
            out = transcribe_words(audio, model_name="large-v3", language="ru")

        assert out == nvidia_words
        mock_nvidia.assert_called_once()
        assert mock_nvidia.call_args.kwargs["model_name"] == "nvidia/parakeet-tdt-0.6b-v3"
    finally:
        monkeypatch.undo()


def test_transcribe_words_fallback_whisper_when_nemo_missing(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "asr_backend", "nvidia")
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF")
    whisper_words = [WordTS("test", 0.0, 1.0, 0.9)]

    try:
        with (
            patch("app.services.nvidia_asr.nvidia_asr_available", return_value=False),
            patch(
                "app.services.asr.transcribe_words_whisper",
                return_value=whisper_words,
            ) as mock_whisper,
        ):
            out = transcribe_words(audio, language="ru")

        assert out == whisper_words
        mock_whisper.assert_called_once()
    finally:
        monkeypatch.undo()
