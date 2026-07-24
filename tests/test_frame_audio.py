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
    frame_clips_from_whisper,
    has_all_frame_audio,
)
from app.services.mapper import FrameTiming
from app.services.whisper import WordTS


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

    cells = [(1, "Привет мир")]
    words = [WordTS("привет", 0.0, 0.5, 1.0), WordTS("мир", 0.5, 1.0, 1.0)]
    timings = [FrameTiming(1, 0.0, 10.0, 10.0)]
    cues = build_subtitle_cues_from_cells(
        cells, words, timings, max_words=1, max_end_ts=1.0,
    )
    assert cues
    assert cues[-1][1] <= 1.0


def test_has_all_frame_audio_false_when_missing(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    assert not has_all_frame_audio(audio_dir, [1, 2])


def test_frame_clips_from_whisper_fills_master_duration(tmp_path: Path) -> None:
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"x")
    cells = [(1, "один два"), (2, "три четыре")]
    words = [
        WordTS("один", 0.0, 1.0, 1.0),
        WordTS("два", 1.0, 2.0, 1.0),
        WordTS("три", 2.0, 3.0, 1.0),
        WordTS("четыре", 3.0, 4.0, 1.0),
    ]
    clips = frame_clips_from_whisper(cells, words, master=10.0, voice_full_path=voice)
    assert len(clips) == 2
    assert clips[0].start_ts == 0.0
    assert clips[-1].end_ts == 10.0
    assert abs(sum(c.duration for c in clips) - 10.0) < 0.01


def test_frame_clips_from_whisper_no_r15_crumb_tail(tmp_path: Path) -> None:
    """Как #26: direct word-map overlap + enforce_monotonic не пишут 0.05–0.1s в R15."""
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"x")
    # Много кадров, Whisper «заканчивается» рано — старый путь давал крошки в хвосте.
    cells = [(i, f"кадр номер {i} длинная фраза закадра") for i in range(1, 41)]
    words: list[WordTS] = []
    t = 0.0
    for i in range(80):
        words.append(WordTS(f"слово{i}", t, t + 0.4, 1.0))
        t += 0.4
    master = 200.0
    clips = frame_clips_from_whisper(cells, words, master=master, voice_full_path=voice)
    assert len(clips) == 40
    assert clips[-1].end_ts == master
    crumbs = [c for c in clips if c.duration <= 0.1 + 1e-9]
    assert crumbs == [], f"crumb timings: {[(c.frame_number, c.duration) for c in crumbs[:8]]}"
    # Кадры получают долю от длины речи, не каскад 0.05s.
    assert min(c.duration for c in clips) > 0.5
