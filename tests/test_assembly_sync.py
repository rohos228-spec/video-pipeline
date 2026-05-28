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
    assert cues[0][2] == "Привет"
    assert cues[1][2] == "мир"
    assert cues[0][0] < cues[1][0]
    assert cues[0][1] <= cues[1][0]


def test_fallback_when_no_whisper_in_frame_window() -> None:
    cells = [(1, "один два три")]
    words = [WordTS("шум", 5.0, 5.5, 1.0)]
    timings = [FrameTiming(1, 0.0, 3.0, 3.0)]
    cues = build_subtitle_cues_from_cells(cells, words, timings, max_words=1)
    assert len(cues) == 3
    assert cues[0][2] == "один"
    assert cues[2][2] == "три"


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
    assert len(cues) == 2
    assert cues[0][0] <= 0.2
    assert cues[1][0] > cues[0][0]
    assert cues[0][1] - cues[0][0] >= 0.28


def test_no_long_gap_when_whisper_cluster_is_late_in_frame() -> None:
    """Слова не должны ждать конца кадра, если Whisper сжал их в хвост окна."""
    cells = [(1, "один два три"), (2, "четыре пять шесть")]
    words = [
        WordTS("один", 0.0, 0.3, 1.0),
        WordTS("два", 0.35, 0.6, 1.0),
        WordTS("три", 0.65, 0.9, 1.0),
        WordTS("четыре", 5.0, 5.3, 1.0),
        WordTS("пять", 5.35, 5.6, 1.0),
        WordTS("шесть", 5.65, 5.9, 1.0),
    ]
    timings = [
        FrameTiming(1, 0.0, 2.0, 2.0),
        FrameTiming(2, 2.0, 5.5, 3.5),
    ]
    cues = build_subtitle_cues_from_cells(cells, words, timings, lead_seconds=0.0)
    assert len(cues) == 6
    for prev, cur in zip(cues, cues[1:]):
        gap = cur[0] - prev[1]
        assert gap <= 0.6, f"пауза {gap:.2f}s между {prev[2]!r} и {cur[2]!r}"
        assert cur[0] - prev[0] >= 0.15, f"слишком быстро: {prev[2]!r} → {cur[2]!r}"


def test_duplicate_whisper_indices_not_machine_gun() -> None:
    cells = [(1, "раз два три")]
    words = [
        WordTS("раз", 0.0, 0.4, 1.0),
        WordTS("два", 0.45, 0.8, 1.0),
    ]
    timings = [FrameTiming(1, 0.0, 3.0, 3.0)]
    spans = build_frame_word_spans_per_frame(cells, words, timings)
    assert spans[0].whisper_indices == [0, 1, 1]
    cues = build_subtitle_cues_from_cells(cells, words, timings, lead_seconds=0.0)
    assert len(cues) == 3
    for start, end, _ in cues:
        assert end - start >= 0.28


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
