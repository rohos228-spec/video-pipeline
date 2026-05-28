"""Tests for per-frame audio (variant B)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.frame_audio import (
    FrameAudioClip,
    _rescale_clips_to_master,
    concat_mp3_files,
    delete_frame_audio_files,
    frame_audio_path,
)
from app.services.mapper import FrameTiming


@pytest.mark.asyncio
async def test_concat_mp3_single_file_copy(tmp_path: Path) -> None:
    src = tmp_path / "a.mp3"
    src.write_bytes(b"fake-mp3")
    out = tmp_path / "full.mp3"
    await concat_mp3_files([src], out)
    assert out.read_bytes() == b"fake-mp3"


def test_frame_audio_paths_and_cleanup(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    p1 = frame_audio_path(audio_dir, 1)
    p2 = frame_audio_path(audio_dir, 30)
    p1.write_bytes(b"1")
    p2.write_bytes(b"2")
    assert p1.name == "frame_001.mp3"
    assert p2.name == "frame_030.mp3"
    assert delete_frame_audio_files(audio_dir) == 2
    assert not list(audio_dir.glob("frame_*.mp3"))


def test_frame_clip_timeline_is_contiguous() -> None:
    clips = [
        FrameAudioClip(1, Path("a.mp3"), "one", 0.0, 2.0, 2.0),
        FrameAudioClip(2, Path("b.mp3"), "two", 2.0, 5.5, 3.5),
        FrameAudioClip(3, Path("c.mp3"), "three", 5.5, 6.0, 0.5),
    ]
    assert clips[0].start_ts == 0.0
    for prev, cur in zip(clips, clips[1:]):
        assert cur.start_ts == prev.end_ts
    assert clips[-1].end_ts == 6.0


def test_rescale_clips_to_master_matches_voice_full() -> None:
    clips = [
        FrameAudioClip(1, Path("a.mp3"), "a", 0.0, 4.0, 4.0),
        FrameAudioClip(2, Path("b.mp3"), "b", 4.0, 8.0, 4.0),
    ]
    out = _rescale_clips_to_master(clips, master=70.0)
    assert out[-1].end_ts == 70.0
    assert abs(sum(c.duration for c in out) - 70.0) < 0.01
    assert out[0].start_ts == 0.0
    for prev, cur in zip(out, out[1:]):
        assert cur.start_ts == prev.end_ts


def test_subtitles_clamped_to_audio_end() -> None:
    from app.services.subtitles import build_subtitle_cues_from_cells
    from app.services.whisper import WordTS

    cells = [(1, "Привет мир")]
    words = [WordTS("привет", 0.0, 0.5, 1.0), WordTS("мир", 0.5, 1.0, 1.0)]
    timings = [FrameTiming(1, 0.0, 10.0, 10.0)]
    cues = build_subtitle_cues_from_cells(
        cells, words, timings, max_words=2, max_end_ts=1.0,
    )
    assert cues
    assert cues[-1][1] <= 1.0
