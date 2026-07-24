"""R15 absolute montage — extend clone gaps, no slow-mo."""

import asyncio
import shutil
from pathlib import Path

import pytest

from app.services.media_probe import probe_duration
from app.services.montage.variant2 import (
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    _clip_filter_chain,
    _clip_needs_loop,
    _concat_segments,
    _duration_up_to_frame,
    _encode_clip_segment,
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
    assert len(clip_segs) == 2
    s0, s1 = clip_segs[0].slot, clip_segs[1].slot
    assert abs(s0.prefix_pad - 3.28) < 0.02
    assert abs(s0.slot_dur - 2.48) < 0.02
    assert abs(clip_segs[0].duration_s - 5.76) < 0.02
    assert abs(s0.r15_start - 3.28) < 0.02
    assert abs(s0.out_start + s0.prefix_pad - s0.r15_start) < 0.02
    assert abs(s1.out_start - 5.76) < 0.02
    assert abs(s1.r15_start - 5.76) < 0.02
    assert abs(clip_segs[1].duration_s - 4.24) < 0.02  # 2.96 R15 + 1.28 voice tail


def test_no_black_gap_between_contiguous_r15_when_src_shorter() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=4.0)
    assert all(s.kind == "clip" for s in segs)
    assert abs(sum(s.duration_s for s in segs) - 4.0) < 0.02


def test_missing_frame_extends_previous_not_black() -> None:
    slots = [
        _OverlaySlot(46, 90.0, 100.0, Path("a.mp4"), "scene"),
        _OverlaySlot(48, 103.0, 108.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 10.0, Path("b.mp4"): 5.0}
    segs = build_timeline_segments(slots, src, voice_s=110.0)
    assert all(s.kind == "clip" for s in segs)
    assert abs(segs[0].slot.suffix_pad - 3.0) < 0.02
    assert abs(segs[0].slot.out_end - 103.0) < 0.02
    assert abs(segs[1].slot.out_start - 103.0) < 0.02
    assert abs(segs[1].slot.suffix_pad - 2.0) < 0.02
    assert abs(sum(s.duration_s for s in segs) - 110.0) < 0.02


def test_filter_chain_uses_clone_not_slowmo() -> None:
    # Короткий src: в окне без freeze-tpad (loop на encode); gap → clone suffix.
    vf = _clip_filter_chain(1920, 1080, slot_dur=2.48, src_dur=2.0)
    assert "trim=duration=2.480" in vf
    assert vf.count("tpad=stop_mode=clone") == 0
    assert "setpts=PTS/" not in vf
    assert _clip_needs_loop(2.0, 2.48)
    vf_gap = _clip_filter_chain(
        1920, 1080, slot_dur=2.0, src_dur=2.0, suffix_pad=3.0
    )
    assert "tpad=stop_mode=clone:stop_duration=3.000" in vf_gap
    assert not _clip_needs_loop(2.0, 2.0)


def test_filter_chain_prefix_after_slot_trim_not_before() -> None:
    """prefix до trim съедал префикс при длинном src — сегмент выходил короче R15+gap."""
    vf = _clip_filter_chain(
        1920, 1080, slot_dur=2.0, src_dur=8.0, prefix_pad=3.0, suffix_pad=1.0
    )
    assert "setpts=PTS/" not in vf
    trim_at = vf.index("trim=duration=2.000")
    prefix_at = vf.index("tpad=start_mode=clone:start_duration=3.000")
    suffix_at = vf.index("tpad=stop_mode=clone:stop_duration=1.000")
    assert trim_at < prefix_at < suffix_at
    assert vf.count("trim=duration=6.000") == 1  # final total = 3+2+1
    assert not _clip_needs_loop(8.0, 2.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
@pytest.mark.asyncio
async def test_ffmpeg_prefix_long_src_keeps_full_duration(tmp_path: Path) -> None:
    """Long src + leading gap: segment must be prefix+slot, not trim-eaten short."""
    clip = tmp_path / "long.mp4"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=green:s=320x240:d=4:r=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(clip),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await proc.wait() == 0

    slots = [_OverlaySlot(1, 3.0, 5.0, clip, "scene")]
    src = {clip: 4.0}
    voice_s = 5.0
    segs = build_timeline_segments(slots, src, voice_s=voice_s)
    assert len(segs) == 1
    assert abs(segs[0].slot.prefix_pad - 3.0) < 0.02
    assert abs(segs[0].duration_s - 5.0) < 0.02

    out = tmp_path / "seg.mp4"
    await _encode_clip_segment(segs[0].slot, out, w=320, h=240)
    got = await probe_duration(out)
    assert abs(got - 5.0) < 0.08


def test_montage_engine_is_v3_slots() -> None:
    assert "slots-concat" in MONTAGE_ENGINE_V2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
@pytest.mark.asyncio
async def test_ffmpeg_slot_timeline_no_black_no_slowmo(tmp_path: Path) -> None:
    """Encode synthetic clips: gap extend + short src clone, duration = voice."""
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    for path, color, dur in (
        (clip_a, "red", 1.0),
        (clip_b, "blue", 2.0),
    ):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240:d={dur}:r=30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert await proc.wait() == 0

    slots = [
        _OverlaySlot(1, 0.0, 2.0, clip_a, "scene"),
        _OverlaySlot(2, 3.0, 5.0, clip_b, "scene"),
    ]
    src = {clip_a: 1.0, clip_b: 2.0}
    voice_s = 6.0
    segs = build_timeline_segments(slots, src, voice_s=voice_s)
    assert all(s.kind == "clip" for s in segs)
    assert abs(segs[0].slot.suffix_pad - 1.0) < 0.02

    seg_paths: list[Path] = []
    for i, seg in enumerate(segs):
        out = tmp_path / f"seg_{i}.mp4"
        assert seg.slot is not None
        await _encode_clip_segment(seg.slot, out, w=320, h=240)
        seg_paths.append(out)

    list_file = tmp_path / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in seg_paths),
        encoding="utf-8",
    )
    timeline = tmp_path / "timeline.mp4"
    await _concat_segments(list_file, timeline, voice_s=voice_s)
    got = await probe_duration(timeline)
    assert abs(got - voice_s) < 0.08
