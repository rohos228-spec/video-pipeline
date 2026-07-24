"""Start-only R15: insert at r15_start; reverse only until next start if src short."""

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


def test_pingpong_forward_then_reverse() -> None:
    parts = _pingpong_plan(2.0, 5.0, start_phase="fwd")
    assert parts[0] == ("fwd", 2.0)
    assert parts[1][0] == "rev"


def test_insert_at_start_reverse_until_next_start() -> None:
    """Только старты: клип @ start; если src короче следующего старта → reverse."""
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.0, 8.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 4.0, Path("b.mp4"): 3.0}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert abs(bound[0].slot.out_start - 0.0) < 0.02
    assert abs(bound[0].duration_s - 5.0) < 0.02  # до старта 2, не до r15_end=2
    plan = _pingpong_plan(4.0, 5.0, start_phase="fwd")
    assert plan[0] == ("fwd", 4.0) and plan[1][0] == "rev"


def test_micro_gap_starts_do_not_crash_validation() -> None:
    """Регрессия #26: схлопнутые старты 239.84→239.89 не роняют validate."""
    slots = [
        _OverlaySlot(62, 200.0, 221.0, Path("a.mp4"), "scene"),
        _OverlaySlot(63, 239.84, 239.90, Path("b.mp4"), "shot1"),
        _OverlaySlot(63, 239.89, 245.0, Path("c.mp4"), "shot2"),
        _OverlaySlot(64, 239.90, 245.0, Path("d.mp4"), "scene"),
        _OverlaySlot(65, 250.0, 255.0, Path("e.mp4"), "scene"),
    ]
    src = {Path(p): 5.0 for p in ("a.mp4", "b.mp4", "c.mp4", "d.mp4", "e.mp4")}
    # раньше: RuntimeError длина 0.05 != места 0.00
    segs = build_timeline_segments(slots, src, voice_s=260.0)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert bound
    assert abs(sum(s.duration_s for s in segs) - 260.0) < 0.05
    for s in bound:
        assert s.slot.out_start == pytest.approx(s.slot.r15_start, abs=0.03)


def test_never_unbind_from_r15_start() -> None:
    slots = [
        _OverlaySlot(1, 3.28, 5.76, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 5.76, 8.72, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 1.5}
    segs = build_timeline_segments(slots, src, voice_s=10.0)
    bound = [s for s in segs if s.slot and s.slot.bound_to_r15]
    assert abs(bound[0].slot.out_start - 3.28) < 0.02
    assert abs(bound[1].slot.out_start - 5.76) < 0.02


def test_gap_policy_name() -> None:
    assert GAP_POLICY == "absolute_r15_start_then_reverse"
    assert "tpad" not in _clip_filter_chain(1920, 1080, 2.0, 8.0)
    assert "slots-concat" in MONTAGE_ENGINE_V2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
@pytest.mark.asyncio
async def test_ffmpeg_reverse_to_next_start(tmp_path: Path) -> None:
    clip = tmp_path / "a.mp4"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "color=c=red:s=320x240:d=2.0:r=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(clip),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await proc.wait() == 0
    slots = [
        _OverlaySlot(1, 0.0, 1.0, clip, "scene"),
        _OverlaySlot(2, 5.0, 6.0, clip, "scene"),
    ]
    src = {clip: 2.0}
    segs = build_timeline_segments(slots, src, voice_s=5.0)
    bound0 = next(s for s in segs if s.slot and s.slot.bound_to_r15)
    assert abs(bound0.duration_s - 5.0) < 0.02
    out = tmp_path / "seg.mp4"
    await _encode_clip_segment(bound0.slot, out, w=320, h=240)
    assert abs(await probe_duration(out) - 5.0) < 0.25
