"""Поиск mp3/wav для озвучки и BGM."""

from dataclasses import dataclass, field
from pathlib import Path

from app.services.assembly_inputs import (
    is_supported_audio,
    resolve_bgm_path,
    resolve_voice_path,
)


@dataclass
class _FakeProject:
    data_dir: Path
    meta: dict = field(default_factory=dict)


def test_is_supported_audio(tmp_path: Path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    assert is_supported_audio(mp3)
    assert is_supported_audio(tmp_path / "a.WAV") is False  # file missing
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x")
    assert is_supported_audio(wav)
    assert not is_supported_audio(tmp_path / "a.m4a")


def test_resolve_voice_wav(tmp_path: Path):
    audio = tmp_path / "audio"
    audio.mkdir()
    wav = audio / "voice_manual.wav"
    wav.write_bytes(b"\x00")
    project = _FakeProject(data_dir=tmp_path)
    assert resolve_voice_path(project) == wav


def test_resolve_bgm_mp3(tmp_path: Path):
    audio = tmp_path / "audio"
    audio.mkdir()
    mp3 = audio / "bgm.mp3"
    mp3.write_bytes(b"\x00")
    project = _FakeProject(data_dir=tmp_path)
    assert resolve_bgm_path(project) == mp3
