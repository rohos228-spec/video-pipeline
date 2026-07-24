"""R15 absolute montage — full window, slow when src short."""

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


def test_clip_fills_full_r15_window_on_timeline() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 2.0, Path("b.mp4"): 2.96}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    clip_segs = [s for s in segs if s.kind == "clip"]
    assert abs(clip_segs[0].duration_s - 2.48) < 0.02
    assert abs(clip_segs[1].duration_s - 2.96) < 0.02
    assert clip_segs[0].slot.out_end == 5.76
    assert clip_segs[1].slot.out_start == 5.76


def test_no_black_gap_between_contiguous_r15_when_src_shorter() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=4.0)
    assert all(s.kind == "clip" for s in segs)
    assert abs(sum(s.duration_s for s in segs) - 4.0) < 0.02


def test_missing_frame_still_black_gap() -> None:
    slots = [
        _OverlaySlot(46, 90.0, 100.0, Path("a.mp4"), "scene"),
        _OverlaySlot(48, 103.0, 108.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 10.0, Path("b.mp4"): 5.0}
    segs = build_timeline_segments(slots, src, voice_s=110.0)
    kinds = [s.kind for s in segs]
    assert kinds.count("black") >= 1


def test_montage_engine_is_v3_slots() -> None:
    assert "slots-concat" in MONTAGE_ENGINE_V2
