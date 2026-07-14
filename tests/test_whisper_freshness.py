"""Тесты whisper cache / freshness."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.whisper import whisper_words_fresh_for_audio


def test_whisper_words_fresh_when_newer_than_audio(tmp_path: Path) -> None:
    audio = tmp_path / "voice_full.mp3"
    words = tmp_path / "words.json"
    audio.write_bytes(b"a")
    words.write_text("[]")
    words.touch()
    import os
    import time

    time.sleep(0.02)
    os.utime(audio, (audio.stat().st_mtime - 10, audio.stat().st_mtime - 10))

    art = SimpleNamespace(path=str(words))
    assert whisper_words_fresh_for_audio(art, audio) is True


def test_whisper_words_stale_when_older_than_audio(tmp_path: Path) -> None:
    audio = tmp_path / "voice_full.mp3"
    words = tmp_path / "words.json"
    words.write_text("[]")
    audio.write_bytes(b"a" * 100)
    import os
    import time

    time.sleep(0.02)
    os.utime(words, (words.stat().st_mtime - 10, words.stat().st_mtime - 10))

    art = SimpleNamespace(path=str(words))
    assert whisper_words_fresh_for_audio(art, audio) is False


def test_whisper_words_fresh_when_artifact_missing(tmp_path: Path) -> None:
    audio = tmp_path / "voice_full.mp3"
    audio.write_bytes(b"a")
    assert whisper_words_fresh_for_audio(None, audio) is False
