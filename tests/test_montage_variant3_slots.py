"""Absolute R15 + forward-then-reverse fill when src shorter than window."""

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


def test_never_unbind_from_r15() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 1.5}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert abs(bound[0].slot.out_start - 3.28) < 0.02
    assert abs(bound[0].duration_s - 2.48) < 0.02  # полное окно R15
    assert abs(bound[1].slot.out_start - 5.76) < 0.02
    assert abs(bound[1].duration_s - 2.96) < 0.02
    assert abs(sum(s.duration_s for s in segs) - 10.0) < 0.02


def test_gap_keeps_next_on_r15() -> None:
    slots = [
        _OverlaySlot(46, 90.0, 100.0, Path("a.mp4"), "scene"),
        _OverlaySlot(48, 103.0, 108.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 10.0, Path("b.mp4"): 5.0}
    segs = build_timeline_segments(slots, src, voice_s=110.0)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert abs(bound[0].slot.out_start - 90.0) < 0.02
    assert abs(bound[1].slot.out_start - 103.0) < 0.02
    fills = [s for s in segs if s.slot and not s.slot.bound_to_r15]
    assert any(abs(s.duration_s - 3.0) < 0.02 for s in fills)


def test_filter_no_freeze_no_slowmo() -> None:
    vf = _clip_filter_chain(1920, 1080, 2.0, 8.0)
    assert "tpad" not in vf
    assert "setpts=PTS/" not in vf
    assert GAP_POLICY == "absolute_r15_reverse_fill"
    assert "slots-concat" in MONTAGE_ENGINE_V2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
@pytest.mark.asyncio
async def test_ffmpeg_reverse_fill_duration(tmp_path: Path) -> None:
    clip = tmp_path / "a.mp4"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "color=c=red:s=320x240:d=1.0:r=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(clip),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await proc.wait() == 0

    slots = [_OverlaySlot(1, 0.0, 3.0, clip, "scene")]
    src = {clip: 1.0}
    voice_s = 3.0
    segs = build_timeline_segments(slots, src, voice_s=voice_s)
    assert len(segs) == 1
    assert abs(segs[0].duration_s - 3.0) < 0.02

    out = tmp_path / "seg.mp4"
    await _encode_clip_segment(segs[0].slot, out, w=320, h=240)
    got = await probe_duration(out)
    assert abs(got - 3.0) < 0.12

    list_file = tmp_path / "concat.txt"
    list_file.write_text(f"file '{out.as_posix()}'\n", encoding="utf-8")
    timeline = tmp_path / "timeline.mp4"
    await _concat_segments(list_file, timeline, voice_s=voice_s)
    assert abs(await probe_duration(timeline) - voice_s) < 0.12
