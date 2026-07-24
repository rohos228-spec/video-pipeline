"""Overlay R15 plan — absolute start_s, extend clone to next start, no slow-mo."""

from pathlib import Path

from app.services.montage.variant2 import (
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    _duration_up_to_frame,
    build_timeline_segments,
)


def test_duration_up_to_frame() -> None:
    assert _duration_up_to_frame(0.0) == 0.0
    assert abs(_duration_up_to_frame(0.001) - 1 / 30) < 1e-6


def test_display_dur_covers_voice() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.96, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.96, 5.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 8.0, Path("b.mp4"): 8.0}
    voice_s = 10.0
    plans = build_timeline_segments(slots, src, voice_s=voice_s)
    assert abs(plans[0].display_dur - 2.96) < 0.02
    assert abs(plans[1].display_dur - (voice_s - 2.96)) < 0.02


def test_short_src_still_on_r15_start() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 2.0}
    plans = build_timeline_segments(slots, src, voice_s=4.0)
    assert abs(plans[0].slot.start_s - 0.0) < 0.02
    assert abs(plans[1].slot.start_s - 2.0) < 0.02
    assert plans[0].display_dur > plans[0].r15_window - 0.02


def test_first_frame_on_r15_not_at_zero() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.96}
    plans = build_timeline_segments(slots, src, voice_s=10.0)
    assert abs(plans[0].slot.start_s - 3.28) < 0.02
    assert abs(plans[1].slot.start_s - 5.76) < 0.02


def test_r15_gap_extends_display_not_black() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.5, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.0}
    plans = build_timeline_segments(slots, src, voice_s=4.0)
    assert plans[0].gap_extend > 0.4
    assert abs(plans[0].display_dur - 2.5) < 0.02


def test_missing_frame_extends_display() -> None:
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
    plans = build_timeline_segments(slots, src, voice_s=110.0)
    frame46 = next(p for p in plans if p.slot.frame_number == 46)
    assert frame46.gap_extend > 2.5
    assert abs(frame46.display_dur - 13.0) < 0.02


def test_montage_engine_is_overlay() -> None:
    assert "overlay" in MONTAGE_ENGINE_V2
