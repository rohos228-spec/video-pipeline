"""Tests for Whisper/Excel alignment, subtitles, and frame timing."""

from __future__ import annotations

from app.services.assembly import subtitles_vf_arg
from app.services.mapper import (
    FrameTiming,
    build_frame_word_spans_per_frame,
    map_frames,
    normalize_contiguous,
)
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


def test_map_frames_redistributes_when_whisper_runs_out() -> None:
    cells = [(i, f"слово{i}") for i in range(1, 7)]
    words = [WordTS(f"слово{i}", float(i - 1), float(i), 1.0) for i in range(1, 4)]
    timings = map_frames(cells, words, audio_duration=12.0)
    assert len(timings) == 6
    assert timings[-1].end_ts == 12.0


def test_one_word_per_cue_uses_next_word_start_as_end() -> None:
    cells = [(1, "Привет мир")]
    words = [
        WordTS("привет", 0.10, 0.50, 1.0),
        WordTS("мир", 0.52, 0.90, 1.0),
    ]
    timings = [FrameTiming(1, 0.0, 1.0, 1.0)]
    cues = build_subtitle_cues_from_cells(
        cells, words, timings, max_words=1, lead_seconds=0.0,
    )
    assert len(cues) == 2
    assert cues[0] == (0.1, 0.5, "Привет")  # end = 0.52 - 0.02
    assert cues[1][2] == "мир"
    assert cues[1][0] == 0.52


def test_one_word_lead_shows_earlier() -> None:
    cells = [(1, "Раз два")]
    words = [
        WordTS("раз", 0.20, 0.50, 1.0),
        WordTS("два", 0.55, 0.90, 1.0),
    ]
    timings = [FrameTiming(1, 0.0, 1.0, 1.0)]
    cues = build_subtitle_cues_from_cells(
        cells, words, timings, max_words=1, lead_seconds=0.10,
    )
    assert cues[0][0] == 0.1
    assert cues[1][0] == 0.45


def test_per_frame_alignment_ignores_words_outside_window() -> None:
    cells = [(1, "один"), (2, "два")]
    words = [
        WordTS("один", 0.0, 0.4, 1.0),
        WordTS("шум", 0.5, 0.9, 1.0),
        WordTS("два", 1.0, 1.4, 1.0),
    ]
    timings = [
        FrameTiming(1, 0.0, 1.0, 1.0),
        FrameTiming(2, 1.0, 2.0, 1.0),
    ]
    spans = build_frame_word_spans_per_frame(cells, words, timings)
    assert spans[0].whisper_indices == [0]
    assert spans[1].whisper_indices == [2]


def test_subtitles_vf_arg_is_bare_filename_without_path_separators() -> None:
    vf = subtitles_vf_arg()
    assert vf == "subtitles=subs.ass"
    assert ":" not in vf
