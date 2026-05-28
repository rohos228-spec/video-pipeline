"""Tests for Whisper/Excel alignment, subtitles, and frame timing."""

from __future__ import annotations

from app.services.assembly import subtitles_vf_arg
from app.services.mapper import map_frames, normalize_contiguous, FrameTiming
from app.services.subtitles import build_subtitle_cues_from_cells
from app.services.whisper import WordTS


def test_normalize_contiguous_fills_full_audio_and_no_gaps() -> None:
    raw = [
        FrameTiming(1, 0.0, 2.0, 2.0),
        FrameTiming(2, 5.0, 6.0, 1.0),
        FrameTiming(3, 0.0, 0.0, 0.0),
    ]
    out = normalize_contiguous(raw, audio_duration=60.0)
    assert out[0].start_ts == 0.0
    assert out[-1].end_ts == 60.0
    assert abs(sum(t.duration for t in out) - 60.0) < 0.01
    for prev, cur in zip(out, out[1:]):
        assert abs(cur.start_ts - prev.end_ts) < 0.001


def test_map_frames_redistributes_when_whisper_runs_out() -> None:
    cells = [(i, f"слово{i}") for i in range(1, 7)]
    words = [WordTS(f"слово{i}", float(i - 1), float(i), 1.0) for i in range(1, 4)]
    timings = map_frames(cells, words, audio_duration=12.0)
    assert len(timings) == 6
    assert timings[-1].end_ts == 12.0
    assert all(t.duration > 0 for t in timings)


def test_subtitles_use_excel_text_max_two_words() -> None:
    cells = [(1, "Привет мир"), (2, "Новый кадр")]
    words = [
        WordTS("привет", 0.0, 0.5, 1.0),
        WordTS("мир", 0.5, 1.0, 1.0),
        WordTS("новый", 1.0, 1.4, 1.0),
        WordTS("кадр", 1.4, 1.8, 1.0),
    ]
    timings = map_frames(cells, words, audio_duration=2.0)
    cues = build_subtitle_cues_from_cells(cells, words, timings, max_words=2)
    assert ("Привет", "мир") == tuple(cues[0][2].split())
    assert cues[0][0] == timings[0].start_ts
    assert cues[1][2] == "Новый кадр"


def test_subtitles_vf_arg_is_bare_filename_without_path_separators() -> None:
    vf = subtitles_vf_arg()
    assert vf == "subtitles=subs.ass"
    assert ":" not in vf
    assert "/" not in vf
    assert "\\" not in vf
