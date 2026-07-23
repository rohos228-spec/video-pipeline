"""Поиск готовой озвучки на диске."""

from __future__ import annotations

import time
from pathlib import Path

from app.services.frame_audio import find_voice_full_on_disk


def test_find_voice_mp3_in_audio_dir(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    audio.mkdir()
    voice = audio / "voice.mp3"
    voice.write_bytes(b"mp3")

    found = find_voice_full_on_disk(tmp_path)
    assert found == voice


def test_find_voice_montage_legacy_name(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    audio.mkdir()
    voice = audio / "voice_montage.mp3"
    voice.write_bytes(b"mp3")
    found = find_voice_full_on_disk(tmp_path)
    assert found == voice


def test_find_voice_via_montage_meta_hint(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    audio.mkdir()
    voice = audio / "custom_upload.wav"
    voice.write_bytes(b"wav")
    found = find_voice_full_on_disk(
        tmp_path,
        meta={"montage_voice_path": str(voice)},
    )
    assert found == voice


def test_find_voice_wav_voice_full_prefix(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    audio.mkdir()
    voice = audio / "voice_full_002.wav"
    voice.write_bytes(b"wav")

    found = find_voice_full_on_disk(tmp_path)
    assert found == voice


def test_ignores_frame_clips(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "frame_001.mp3").write_bytes(b"x")
    voice = audio / "voice.wav"
    voice.write_bytes(b"wav")

    found = find_voice_full_on_disk(tmp_path)
    assert found == voice


def test_find_voiceover_in_project_root(tmp_path: Path) -> None:
    voice = tmp_path / "voiceover.mp3"
    voice.write_bytes(b"mp3")

    found = find_voice_full_on_disk(tmp_path)
    assert found == voice


def test_prefers_newest_candidate(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    audio.mkdir()
    old = audio / "voice.mp3"
    old.write_bytes(b"old")
    newer = audio / "voice_full.mp3"
    newer.write_bytes(b"new")
    time.sleep(0.02)
    newer.touch()

    found = find_voice_full_on_disk(tmp_path)
    assert found == newer
