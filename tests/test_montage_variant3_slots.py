"""Slot-based R15 montage timeline (variant 3) — no gaps, extend previous."""

from pathlib import Path

from app.services.montage.variant2 import (
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    build_continuous_slots,
    build_timeline_segments,
)


def test_build_continuous_slots_extends_over_r15_gap() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 7.0, 9.0, Path("b.mp4"), "scene"),
    ]
    cont = build_continuous_slots(slots, voice_s=12.0)
    assert len(cont) == 2
    assert cont[0].out_start == 0.0
    assert abs(cont[0].out_end - 7.0) < 0.02
    assert abs(cont[0].suffix_pad - (7.0 - 5.76)) < 0.02
    assert abs(cont[1].out_start - 7.0) < 0.02
    assert abs(cont[1].out_end - 12.0) < 0.02


def test_build_continuous_slots_covers_missing_frame_window() -> None:
    """Кадр 47 без clip: кадр 46 тянется до start кадра 48."""
    slots = [
        _OverlaySlot(46, 90.0, 100.0, Path("a.mp4"), "scene"),
        _OverlaySlot(48, 103.0, 108.0, Path("b.mp4"), "scene"),
    ]
    cont = build_continuous_slots(slots, voice_s=110.0)
    assert abs(cont[0].out_end - 103.0) < 0.02
    assert abs(cont[0].suffix_pad - 3.0) < 0.02


def test_build_timeline_segments_no_black_gaps() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, Path("b.mp4"), "scene"),
    ]
    segs = build_timeline_segments(slots, voice_s=10.0)
    assert all(s.kind == "clip" for s in segs)
    assert abs(segs[0].duration_s - 5.76) < 0.02
    assert abs(segs[0].slot.prefix_pad - 3.28) < 0.02
    assert abs(segs[1].duration_s - 4.24) < 0.02
    total = sum(s.duration_s for s in segs)
    assert abs(total - 10.0) < 0.05


def test_montage_engine_is_v3_slots() -> None:
    assert "slots-concat" in MONTAGE_ENGINE_V2
