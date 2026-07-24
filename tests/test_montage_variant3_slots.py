"""R15 start binding + real src duration; reverse only when file is actually short."""

import asyncio
import shutil
from pathlib import Path

import pytest

from app.services.media_probe import probe_duration
from app.services.montage.variant2 import (
    GAP_POLICY,
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    _clip_filter_chain,
    _concat_segments,
    _duration_up_to_frame,
    _encode_clip_segment,
    _pingpong_plan,
    build_timeline_segments,
)


def test_duration_up_to_frame() -> None:
    assert _duration_up_to_frame(0.0) == 0.0
    assert abs(_duration_up_to_frame(0.05) - 2 / 30) < 1e-6


def test_pingpong_plan_short_src_forward_then_reverse() -> None:
    parts = _pingpong_plan(2.0, 5.0, start_phase="fwd")
    assert parts[0] == ("fwd", 2.0)
    assert parts[1][0] == "rev"
    assert abs(sum(d for _, d in parts) - 5.0) < 0.02


def test_never_cut_real_src_at_r15_end_when_file_continues() -> None:
    """РЕГРЕССИЯ: нельзя считать видео конченым по r15_end, если файл длиннее."""
    # R15-окно кадра 1 = 2s, но до следующего старта 5s; файл 4s → играем 4s, не режем на 2s.
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.0, 8.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 4.0, Path("b.mp4"): 3.0}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert abs(bound[0].slot.out_start - 0.0) < 0.02
    assert abs(bound[0].duration_s - 5.0) < 0.02  # до старта кадра 2, не до r15_end=2
    assert abs(bound[0].slot.src_dur - 4.0) < 0.02
    # reverse только на остаток 1s после реального src=4
    plan = _pingpong_plan(4.0, 5.0, start_phase="fwd")
    assert plan[0] == ("fwd", 4.0)
    assert plan[1][0] == "rev"


def test_never_unbind_from_r15_start() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 1.5}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert abs(bound[0].slot.out_start - 3.28) < 0.02
    assert abs(bound[0].duration_s - 2.48) < 0.02
    assert abs(bound[1].slot.out_start - 5.76) < 0.02
    assert abs(bound[1].duration_s - (10.0 - 5.76)) < 0.02
    assert abs(sum(s.duration_s for s in segs) - 10.0) < 0.02


def test_gap_absorbed_into_prev_available_not_r15_window() -> None:
    slots = [
        _OverlaySlot(46, 90.0, 100.0, Path("a.mp4"), "scene"),
        _OverlaySlot(48, 103.0, 108.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 10.0, Path("b.mp4"): 5.0}
    segs = build_timeline_segments(slots, src, voice_s=110.0)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert abs(bound[0].slot.out_start - 90.0) < 0.02
    assert abs(bound[0].duration_s - 13.0) < 0.02  # 90→103, включая дыру
    assert abs(bound[1].slot.out_start - 103.0) < 0.02


def test_filter_no_freeze_no_slowmo() -> None:
    vf = _clip_filter_chain(1920, 1080, 2.0, 8.0)
    assert "tpad" not in vf
    assert "setpts=PTS/" not in vf
    assert "real_src" in GAP_POLICY
    assert "slots-concat" in MONTAGE_ENGINE_V2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
@pytest.mark.asyncio
async def test_ffmpeg_plays_past_r15_end_when_src_longer(tmp_path: Path) -> None:
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    for path, dur in ((clip_a, 4.0), (clip_b, 2.0)):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=red:s=320x240:d={dur}:r=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert await proc.wait() == 0
        assert abs(await probe_duration(path) - dur) < 0.15

    slots = [
        _OverlaySlot(1, 0.0, 2.0, clip_a, "scene"),
        _OverlaySlot(2, 5.0, 7.0, clip_b, "scene"),
    ]
    src = {clip_a: 4.0, clip_b: 2.0}
    voice_s = 7.0
    segs = build_timeline_segments(slots, src, voice_s=voice_s)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert abs(bound[0].duration_s - 5.0) < 0.02

    out = tmp_path / "seg0.mp4"
    await _encode_clip_segment(bound[0].slot, out, w=320, h=240)
    # 4s forward + 1s reverse ≈ 5s, не обрезано на 2s
    assert abs(await probe_duration(out) - 5.0) < 0.2
