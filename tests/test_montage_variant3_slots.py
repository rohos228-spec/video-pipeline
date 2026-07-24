"""Slot-based R15 montage — dense concat, no freeze of last frame."""

from pathlib import Path

from app.services.montage.variant2 import (
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    build_continuous_slots,
    build_timeline_segments,
)


def test_build_continuous_slots_no_freeze_when_src_shorter() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 7.0, 9.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 3.0}
    cont, total = build_continuous_slots(slots, src)
    assert len(cont) == 2
    assert cont[0].out_start == 0.0
    assert abs(cont[0].play_dur - 2.0) < 0.02
    assert abs(cont[1].out_start - 2.0) < 0.02
    assert abs(cont[1].play_dur - 2.0) < 0.02
    assert abs(total - 4.0) < 0.02


def test_build_continuous_slots_trims_when_src_longer_than_r15() -> None:
    slots = [_OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene")]
    cont, total = build_continuous_slots(slots, {Path("a.mp4"): 5.0})
    assert abs(cont[0].play_dur - 2.0) < 0.02
    assert abs(total - 2.0) < 0.02


def test_build_timeline_segments_black_tail_only_for_voice() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    clip_total = sum(s.duration_s for s in segs if s.kind == "clip")
    assert abs(clip_total - 4.0) < 0.02
    assert segs[-1].kind == "black"
    assert abs(segs[-1].duration_s - 6.0) < 0.02


def test_montage_engine_is_v3_slots() -> None:
    assert "slots-concat" in MONTAGE_ENGINE_V2
