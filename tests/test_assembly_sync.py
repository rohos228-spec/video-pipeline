"""Tests for Whisper word subtitles and assembly timeline."""

from __future__ import annotations

from pathlib import Path

from app.services.assembly import ClipSpec, build_timeline_segments
from app.services.subtitles import build_word_subtitle_cues
from app.services.whisper import WordTS


def test_build_word_subtitle_cues_max_two_words() -> None:
    words = [
        WordTS("один", 0.0, 0.4),
        WordTS("два", 0.4, 0.8),
        WordTS("три", 0.8, 1.1),
        WordTS("четыре", 1.1, 1.5),
    ]
    cues = build_word_subtitle_cues(words, max_words=2)
    assert cues == [
        (0.0, 0.8, "один два"),
        (0.8, 1.5, "три четыре"),
    ]


def test_build_word_subtitle_cues_single_trailing_word() -> None:
    words = [
        WordTS("раз", 1.0, 1.2),
        WordTS("два", 1.2, 1.5),
        WordTS("три", 1.5, 1.8),
    ]
    cues = build_word_subtitle_cues(words, max_words=2)
    assert cues == [
        (1.0, 1.5, "раз два"),
        (1.5, 1.8, "три"),
    ]


def test_build_timeline_segments_inserts_black_gap() -> None:
    clips = [
        ClipSpec(src=Path("a.mp4"), duration=2.0),
        ClipSpec(src=Path("b.mp4"), duration=1.5),
    ]
    segments = build_timeline_segments(
        clips,
        [(0.0, 2.0), (2.5, 4.0)],
    )
    assert [(s.kind, round(s.duration, 3), s.src.name if s.src else None) for s in segments] == [
        ("clip", 2.0, "a.mp4"),
        ("black", 0.5, None),
        ("clip", 1.5, "b.mp4"),
    ]
