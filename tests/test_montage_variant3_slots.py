"""Dense stitch montage — no slow-mo, extend prev on R15 gap, short src stitches next."""

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


def test_short_src_stitches_next_immediately() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=4.0)
    assert all(s.kind == "clip" for s in segs)
    assert abs(segs[0].slot.play_dur - 1.0) < 0.02
    assert abs(segs[1].slot.out_start - 1.0) < 0.02
    assert not segs[0].slot.filled_r15


def test_r15_gap_extends_previous_not_black() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.5, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=4.0)
    assert all(s.kind == "clip" for s in segs)
    assert segs[0].slot.filled_r15
    assert segs[0].slot.suffix_pad > 0.4
    assert abs(segs[0].duration_s - 2.5) < 0.02


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
    assert all(s.kind == "clip" for s in segs)
    frame46 = next(s for s in segs if s.slot and s.slot.frame_number == 46)
    assert frame46.slot.filled_r15
    assert frame46.slot.suffix_pad > 2.5
    assert abs(frame46.duration_s - 13.0) < 0.02


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
