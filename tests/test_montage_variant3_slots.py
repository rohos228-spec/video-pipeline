"""Slot-based R15 montage timeline (variant 3)."""

from app.services.montage.variant2 import (
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    build_timeline_segments,
)


def test_build_timeline_segments_inserts_leading_gap_and_tail() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, __import__("pathlib").Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, __import__("pathlib").Path("b.mp4"), "scene"),
    ]
    segs = build_timeline_segments(slots, voice_s=10.0)
    assert segs[0].kind == "black"
    assert abs(segs[0].duration_s - 3.28) < 0.02
    assert segs[1].kind == "clip"
    assert segs[-1].kind == "black"
    assert abs(segs[-1].duration_s - 1.28) < 0.02
    total = sum(s.duration_s for s in segs)
    assert abs(total - 10.0) < 0.05


def test_montage_engine_is_v3_slots() -> None:
    assert "slots-concat" in MONTAGE_ENGINE_V2
