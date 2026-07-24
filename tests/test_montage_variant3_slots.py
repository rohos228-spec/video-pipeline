"""Absolute R15 montage — extend previous clone, timecodes are priority."""

from pathlib import Path

import pytest

from app.services.montage.variant2 import (
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    _duration_up_to_frame,
    build_timeline_segments,
)


def test_duration_up_to_frame() -> None:
    assert _duration_up_to_frame(0.0) == 0.0
    assert abs(_duration_up_to_frame(0.001) - 1 / 30) < 1e-6
    assert abs(_duration_up_to_frame(0.05) - 2 / 30) < 1e-6
    assert abs(_duration_up_to_frame(2.96) - 89 / 30) < 1e-6


def test_timeline_segments_sum_to_voice() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.96, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.96, 5.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 8.0, Path("b.mp4"): 8.0}
    voice_s = 10.0
    segs = build_timeline_segments(slots, src, voice_s=voice_s)
    total = sum(s.duration_s for s in segs)
    assert abs(total - voice_s) < 0.02
    assert all(s.kind == "clip" for s in segs)


def test_short_src_keeps_r15_start_for_next() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=4.0)
    assert abs(segs[0].duration_s - 2.0) < 0.02
    assert abs(segs[1].slot.out_start - 2.0) < 0.02
    assert abs(segs[1].slot.r15_start - 2.0) < 0.02


def test_first_frame_respects_r15_start() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.96}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    assert abs(segs[0].slot.prefix_pad - 3.28) < 0.02
    assert abs(segs[0].slot.out_start + segs[0].slot.prefix_pad - 3.28) < 0.02
    assert abs(segs[1].slot.out_start - 5.76) < 0.02


def test_r15_gap_extends_previous_not_black() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.5, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=4.0)
    assert all(s.kind == "clip" for s in segs)
    assert segs[0].slot.suffix_pad > 0.4
    assert abs(segs[0].duration_s - 2.5) < 0.02
    assert abs(segs[1].slot.out_start - 2.5) < 0.02


def test_missing_frame_extends_previous_no_black() -> None:
    slots = [
        _OverlaySlot(45, 80.0, 90.0, Path("z.mp4"), "scene"),
        _OverlaySlot(46, 90.0, 100.0, Path("a.mp4"), "scene"),
        _OverlaySlot(48, 103.0, 108.0, Path("b.mp4"), "scene"),
    ]
    src = {
        Path("z.mp4"): 10.0,
        Path("a.mp4"): 10.0,
        Path("b.mp4"): 5.0,
    }
    segs = build_timeline_segments(slots, src, voice_s=110.0)
    frame46 = next(s for s in segs if s.slot and s.slot.frame_number == 46)
    assert frame46.slot.suffix_pad > 2.5
    assert abs(frame46.duration_s - 13.0) < 0.02
    frame48 = next(s for s in segs if s.slot and s.slot.frame_number == 48)
    assert abs(frame48.slot.r15_start - 103.0) < 0.02


def test_cumulative_positions_match_r15_starts() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
        _OverlaySlot(3, 4.5, 7.0, Path("c.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 1.5, Path("c.mp4"): 3.0}
    segs = build_timeline_segments(slots, src, voice_s=7.0)
    cursor = 0.0
    for seg in segs:
        cs = seg.slot
        assert cs is not None
        assert abs(cs.out_start - cursor) < 0.02
        assert abs(cs.out_start + cs.prefix_pad - cs.r15_start) < 0.02
        cursor += seg.duration_s
    assert abs(cursor - 7.0) < 0.02


def test_clip_out_end_matches_r15_or_next_start() -> None:
    slots = [
        _OverlaySlot(1, 10.0, 20.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 22.0, 30.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 5.0, Path("b.mp4"): 5.0}
    segs = build_timeline_segments(slots, src, voice_s=35.0)
    assert abs(segs[0].slot.out_end - 22.0) < 0.02
    assert abs(segs[0].slot.r15_end - 20.0) < 0.02
    assert segs[0].slot.suffix_pad > 1.9


def test_no_black_segments() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=4.0)
    assert all(s.kind == "clip" for s in segs)


def test_montage_engine_is_v3_slots() -> None:
    assert "slots-concat" in MONTAGE_ENGINE_V2
