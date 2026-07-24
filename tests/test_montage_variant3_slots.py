"""Dense natural montage: play=min(src,R15), next clip immediately, no freeze/loop."""

import asyncio
import shutil
from pathlib import Path

import pytest

from app.services.media_probe import probe_duration
from app.services.montage.variant2 import (
    MONTAGE_ENGINE_V2,
    _OverlaySlot,
    _clip_filter_chain,
    _concat_segments,
    _duration_up_to_frame,
    _encode_clip_segment,
    _play_duration,
    build_timeline_segments,
)


def test_duration_up_to_frame() -> None:
    assert _duration_up_to_frame(0.0) == 0.0
    assert abs(_duration_up_to_frame(0.001) - 1 / 30) < 1e-6
    assert abs(_duration_up_to_frame(0.05) - 2 / 30) < 1e-6
    assert abs(_duration_up_to_frame(2.96) - 89 / 30) < 1e-6


def test_play_duration_caps_at_r15_and_src() -> None:
    assert abs(_play_duration(5.0, 8.0) - 5.0) < 1e-6
    assert abs(_play_duration(5.0, 3.0) - 3.0) < 1e-6


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
    # клипы играют по R15 (src длиннее), хвост — black
    assert segs[0].kind == "clip" and abs(segs[0].duration_s - 2.96) < 0.02
    assert segs[1].kind == "clip" and abs(segs[1].duration_s - 2.04) < 0.02
    assert segs[-1].kind == "black"


def test_short_src_starts_next_immediately_no_freeze() -> None:
    slots = [
        _OverlaySlot(1, 0.0, 2.0, Path("a.mp4"), "scene"),
        _OverlaySlot(2, 2.0, 4.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 1.0, Path("b.mp4"): 2.0}
    segs = build_timeline_segments(slots, src, voice_s=4.0)
    clips = [s for s in segs if s.kind == "clip"]
    assert abs(clips[0].duration_s - 1.0) < 0.02
    assert abs(clips[1].slot.out_start - 1.0) < 0.02  # сразу после play, не после R15=2.0
    assert abs(clips[0].slot.suffix_pad) < 0.001
    assert abs(clips[0].slot.prefix_pad) < 0.001
    assert segs[-1].kind == "black"  # 4 - (1+2) = 1s
    assert abs(sum(s.duration_s for s in segs) - 4.0) < 0.02


def test_gap_in_r15_does_not_freeze_previous() -> None:
    """Дыра в Excel не держит предыдущий кадр — следующий сразу после play."""
    slots = [
        _OverlaySlot(46, 90.0, 100.0, Path("a.mp4"), "scene"),
        _OverlaySlot(48, 103.0, 108.0, Path("b.mp4"), "scene"),
    ]
    src = {Path("a.mp4"): 10.0, Path("b.mp4"): 5.0}
    segs = build_timeline_segments(slots, src, voice_s=20.0)
    clips = [s for s in segs if s.kind == "clip"]
    assert abs(clips[0].duration_s - 10.0) < 0.02  # min(10 R15, 10 src)
    assert abs(clips[1].slot.out_start - 10.0) < 0.02
    assert abs(clips[0].slot.suffix_pad) < 0.001
    assert abs(sum(s.duration_s for s in segs) - 20.0) < 0.02


def test_filter_chain_no_freeze_no_loop_no_slowmo() -> None:
    vf = _clip_filter_chain(1920, 1080, slot_dur=2.0, src_dur=8.0)
    assert "trim=duration=2.000" in vf
    assert "tpad" not in vf
    assert "setpts=PTS/" not in vf
    with pytest.raises(RuntimeError, match="pad запрещ"):
        _clip_filter_chain(1920, 1080, slot_dur=2.0, src_dur=2.0, suffix_pad=1.0)


def test_montage_engine_is_v3_slots() -> None:
    assert "slots-concat" in MONTAGE_ENGINE_V2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
@pytest.mark.asyncio
async def test_ffmpeg_dense_short_then_next(tmp_path: Path) -> None:
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
    clips = [s for s in segs if s.kind == "clip"]
    assert abs(clips[0].duration_s - 1.0) < 0.02
    assert abs(clips[1].slot.out_start - 1.0) < 0.02

    seg_paths: list[Path] = []
    for i, seg in enumerate(segs):
        out = tmp_path / f"seg_{i}.mp4"
        if seg.kind == "black":
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s=320x240:d={seg.duration_s:.3f}:r=30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(out),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            assert await proc.wait() == 0
        else:
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
