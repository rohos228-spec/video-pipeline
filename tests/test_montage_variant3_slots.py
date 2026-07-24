"""Slot-based R15 montage — absolute R15 sync, no freeze."""

from pathlib import Path

from app.services.montage.variant2 import (
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    build_timeline_segments,
)


def test_absolute_r15_places_clip_at_start_not_zero() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.48, Path("b.mp4"): 2.96}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    assert segs[0].kind == "black"
    assert abs(segs[0].duration_s - 3.28) < 0.02
    assert segs[1].kind == "clip"
    assert abs(segs[1].slot.out_start - 3.28) < 0.02
    assert segs[2].kind == "clip"
    assert abs(segs[2].slot.out_start - 5.76) < 0.02
    total = sum(s.duration_s for s in segs)
    assert abs(total - 10.0) < 0.05


def test_no_freeze_black_gap_when_src_shorter_than_r15() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 7.0, 9.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    # a: 3.28 + 2.0 = 5.28, gap до 7.0
    assert segs[0].kind == "black" and abs(segs[0].duration_s - 3.28) < 0.02
    assert segs[1].kind == "clip" and abs(segs[1].duration_s - 2.0) < 0.02
    assert segs[2].kind == "black" and abs(segs[2].duration_s - 1.72) < 0.02
    assert segs[3].kind == "clip" and abs(segs[3].slot.out_start - 7.0) < 0.02


def test_missing_frame_window_is_black_gap() -> None:
    slots = [
        _OverlaySlot(46, 90.0, 100.0, Path("a.mp4"), "scene"),
        _OverlaySlot(48, 103.0, 108.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 10.0, Path("b.mp4"): 5.0}
    segs = build_timeline_segments(slots, src, voice_s=110.0)
    # clip46 ends 100, black 100→103, clip48 at 103
    kinds = [s.kind for s in segs]
    assert "black" in kinds
    clip_starts = [s.slot.out_start for s in segs if s.kind == "clip"]
    assert abs(clip_starts[0] - 90.0) < 0.02
    assert abs(clip_starts[1] - 103.0) < 0.02


def test_montage_engine_is_v3_slots() -> None:
    assert "slots-concat" in MONTAGE_ENGINE_V2
