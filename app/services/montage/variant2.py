"""R15 overlay: абсолютные таймкоды (setpts), gap → clone на том же клипе, без slow-mo.

Чёрное полотно = voice_s. Клип появляется ровно на start_s из Excel.
Между end_s и start следующего — продление clone (не чёрный gap).
"""

from __future__ import annotations

import asyncio
import math
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.models import Project
from app.services.bgm import BgmConfig
from app.services.media_probe import probe_duration, probe_video_size
from app.services.montage.r15 import R15Marker, load_r15_markers, write_r15_proof
from app.services.montage.workspace import wipe_montage_workspace
from app.services.shot2_montage import find_scene_clips, shot2_frame_numbers
from app.settings import settings

MONTAGE_ENGINE_V2 = "montage-v2-r15-overlay-extend-s2"
DEFAULT_W, DEFAULT_H = 1920, 1080
OVERLAY_BATCH = 6
_DEFAULT_VOICE_GAIN = 1.0
_DEFAULT_BGM_MIX_RATIO = 0.35

_X264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20"]


@dataclass(frozen=True)
class _OverlaySlot:
    frame_number: int
    start_s: float
    end_s: float
    clip: Path
    kind: str  # scene | shot1 | shot2
    label: str = ""

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class _OverlayPlan:
    slot: _OverlaySlot
    display_dur: float
    src_dur: float

    @property
    def r15_window(self) -> float:
        return self.slot.duration_s

    @property
    def gap_extend(self) -> float:
        return max(0.0, self.display_dur - self.r15_window)


def _marker_slots(
    project: Project,
    marker: R15Marker,
    shot2_nums: set[int],
) -> list[_OverlaySlot] | None:
    videos_dir = project.data_dir / "videos"
    shot1, disk2 = find_scene_clips(videos_dir, marker.frame_number)
    if shot1 is None or not shot1.is_file():
        return None
    if marker.frame_number in shot2_nums and disk2 is not None and disk2.is_file():
        half = marker.duration_s / 2.0
        mid = marker.start_s + half
        return [
            _OverlaySlot(
                marker.frame_number, marker.start_s, mid, shot1, "shot1", marker.label
            ),
            _OverlaySlot(
                marker.frame_number, mid, marker.end_s, disk2, "shot2", marker.label
            ),
        ]
    return [
        _OverlaySlot(
            marker.frame_number,
            marker.start_s,
            marker.end_s,
            shot1,
            "scene",
            marker.label,
        )
    ]


def _all_slots(project: Project, markers: list[R15Marker]) -> list[_OverlaySlot]:
    shot2_nums = shot2_frame_numbers(project)
    slots: list[_OverlaySlot] = []
    split_n = 0
    skipped: list[int] = []
    for m in markers:
        ms = _marker_slots(project, m, shot2_nums)
        if ms is None:
            skipped.append(m.frame_number)
            continue
        if len(ms) == 2:
            split_n += 1
        slots.extend(ms)
    if skipped:
        logger.warning(
            "[#{}] overlay: кадры {} без clip — gap закрывает продление предыдущего",
            project.id,
            skipped,
        )
    if not slots:
        raise RuntimeError("нет ни одного videos/clip_*.mp4 для монтажа")
    if split_n:
        logger.info(
            "[#{}] shot2: {} кадров 50/50 ({} overlay-слотов)",
            project.id,
            split_n,
            len(slots),
        )
    return slots


def build_overlay_plan(
    slots: list[_OverlaySlot],
    src_durations: dict[Path, float],
    voice_s: float,
) -> list[_OverlayPlan]:
    """План overlay: клип на start_s, display до start следующего / voice_s."""
    ordered = sorted(slots, key=lambda s: (s.start_s, s.frame_number))
    valid = [s for s in ordered if s.duration_s >= 0.05]
    plans: list[_OverlayPlan] = []
    for i, slot in enumerate(valid):
        display_dur = (
            valid[i + 1].start_s - slot.start_s
            if i + 1 < len(valid)
            else voice_s - slot.start_s
        )
        if display_dur <= 0.05:
            raise RuntimeError(
                f"кадр {slot.frame_number}: display_dur {display_dur:.2f}s некорректна"
            )
        src_dur = src_durations[slot.clip]
        if src_dur + 0.02 < slot.duration_s:
            logger.debug(
                "overlay: кадр {} src {:.2f}s < R15 {:.2f}s → clone",
                slot.frame_number,
                src_dur,
                slot.duration_s,
            )
        if display_dur > slot.duration_s + 0.05:
            logger.debug(
                "overlay: кадр {} продлён clone {:.2f}s до {:.2f}s",
                slot.frame_number,
                display_dur - slot.duration_s,
                slot.start_s + display_dur,
            )
        plans.append(
            _OverlayPlan(slot=slot, display_dur=display_dur, src_dur=src_dur)
        )
    return plans


def _validate_overlay_plan(plans: list[_OverlayPlan], voice_s: float) -> None:
    if not plans:
        raise RuntimeError("пустой overlay plan")
    for i, plan in enumerate(plans):
        if plan.slot.start_s < -0.02:
            raise RuntimeError(f"кадр {plan.slot.frame_number}: отрицательный start_s")
        if i + 1 < len(plans):
            expected = plans[i + 1].slot.start_s - plan.slot.start_s
            if abs(plan.display_dur - expected) > 0.03:
                raise RuntimeError(
                    f"кадр {plan.slot.frame_number}: display_dur не до next start"
                )
        else:
            expected = voice_s - plan.slot.start_s
            if abs(plan.display_dur - expected) > 0.05:
                raise RuntimeError(
                    f"последний кадр {plan.slot.frame_number}: display_dur != voice tail"
                )


# alias for tests / plan export
def build_timeline_segments(
    slots: list[_OverlaySlot],
    src_durations: dict[Path, float],
    voice_s: float,
) -> list[_OverlayPlan]:
    plans = build_overlay_plan(slots, src_durations, voice_s)
    _validate_overlay_plan(plans, voice_s)
    return plans


async def _run(cmd: list[str], *, context: str = "") -> None:
    logger.debug("$ {}", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="ignore").strip()
        head = f"ffmpeg exit {proc.returncode}"
        if context:
            head += f" — {context}"
        raise RuntimeError(f"{head}\n" + "\n".join(err.splitlines()[-18:]))


def _scale_chain(
    w: int,
    h: int,
    display_dur: float,
    src_dur: float,
    r15_window: float,
    start_s: float,
) -> str:
    """Клип на start_s; 1x; src короче / gap → clone; длиннее R15 → trim."""
    chain = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
    )
    play = min(src_dur, r15_window)
    if src_dur > r15_window + 0.05:
        chain += f",trim=duration={r15_window:.3f}"
    extend = display_dur - play
    if extend > 0.02:
        chain += f",tpad=stop_mode=clone:stop_duration={extend:.3f}"
    chain += f",trim=duration={display_dur:.3f},setpts=PTS-STARTPTS+{start_s:.3f}/TB"
    return chain


def _write_plan(
    plans: list[_OverlayPlan],
    path: Path,
    *,
    voice_s: float,
    marker_count: int,
) -> None:
    lines = [
        f"engine={MONTAGE_ENGINE_V2}",
        f"voice_duration={voice_s:.3f}",
        f"markers={marker_count}",
        f"overlay_slots={len(plans)}",
        "gap_policy=overlay_absolute_r15_extend_clone",
        "",
        "frame\tkind\texcel\tstart_s\tend_s\tr15_dur\tdisplay_dur\tgap_ext\tsrc\tclip",
    ]
    clip_total = 0.0
    for plan in plans:
        s = plan.slot
        clip_total += s.duration_s
        lines.append(
            f"{s.frame_number}\t{s.kind}\t{s.label}\t{s.start_s:.3f}\t{s.end_s:.3f}\t"
            f"{s.duration_s:.3f}\t{plan.display_dur:.3f}\t{plan.gap_extend:.3f}\t"
            f"{plan.src_dur:.2f}\t{s.clip.name}"
        )
    lines.append(f"\nclip_slots_total={clip_total:.3f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _black_base(path: Path, *, w: int, h: int, dur: float) -> None:
    await _run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s={w}x{h}:d={dur:.3f}:r=30",
        *_X264,
        "-an",
        "-t", f"{dur:.3f}",
        str(path),
    ], context=f"black base {dur:.1f}s")


async def _overlay_batch(
    base: Path,
    batch: list[_OverlayPlan],
    *,
    w: int,
    h: int,
    voice_s: float,
    out: Path,
    batch_idx: int,
) -> None:
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(base)]
    for plan in batch:
        cmd.extend(["-i", str(plan.slot.clip)])

    parts: list[str] = []
    prev = "0:v"
    for j, plan in enumerate(batch):
        tag = f"c{batch_idx}_{j}"
        out_tag = f"v{batch_idx}_{j}"
        parts.append(
            f"[{j + 1}:v]{_scale_chain(w, h, plan.display_dur, plan.src_dur, plan.r15_window, plan.slot.start_s)}[{tag}]"
        )
        parts.append(f"[{prev}][{tag}]overlay=0:0:eof_action=pass[{out_tag}]")
        prev = out_tag

    cmd.extend([
        "-filter_complex", ";".join(parts),
        "-map", f"[{prev}]",
        *_X264,
        "-an",
        "-t", f"{voice_s:.3f}",
        str(out),
    ])
    await _run(cmd, context=f"overlay batch {batch_idx} ({len(batch)} clips)")


async def _build_overlay_timeline(
    project: Project,
    plans: list[_OverlayPlan],
    *,
    w: int,
    h: int,
    voice_s: float,
    tmp: Path,
) -> Path:
    current = tmp / "base_black.mp4"
    await _black_base(current, w=w, h=h, dur=voice_s)

    batch_idx = 0
    for i in range(0, len(plans), OVERLAY_BATCH):
        chunk = plans[i : i + OVERLAY_BATCH]
        nxt = tmp / f"layer_{batch_idx:03d}.mp4"
        await _overlay_batch(
            current,
            chunk,
            w=w,
            h=h,
            voice_s=voice_s,
            out=nxt,
            batch_idx=batch_idx,
        )
        current = nxt
        batch_idx += 1
        logger.info(
            "[#{}] overlay batch {} done ({}/{} slots)",
            project.id,
            batch_idx,
            min(i + OVERLAY_BATCH, len(plans)),
            len(plans),
        )

    got = await probe_duration(current)
    if abs(got - voice_s) > 0.4:
        raise RuntimeError(f"overlay timeline {got:.2f}s != voice {voice_s:.2f}s")
    return current


async def _mux(
    video: Path,
    voice: Path,
    out: Path,
    *,
    voice_s: float,
    bgm: BgmConfig | None,
) -> None:
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(video), "-i", str(voice)]
    gain = max(
        float(getattr(settings, "assembly_voice_gain", _DEFAULT_VOICE_GAIN)),
        0.1,
    )
    bgm_ratio = float(
        getattr(settings, "assembly_bgm_mix_ratio", _DEFAULT_BGM_MIX_RATIO)
    )
    if bgm is not None and bgm.path.is_file():
        bgm_gain = max(bgm.level, 0.0) * max(bgm_ratio, 0.0)
        fc = (
            f"[1:a]volume={gain:.4f}[vox];"
            f"[2:a]volume={bgm_gain:.4f},atrim=0:{voice_s:.3f},asetpts=PTS-STARTPTS[bgm];"
            f"[vox][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd.extend(["-stream_loop", "-1", "-i", str(bgm.path), "-filter_complex", fc])
        cmd.extend(["-map", "0:v:0", "-map", "[aout]"])
    else:
        cmd.extend(["-filter_complex", f"[1:a]volume={gain:.4f}[aout]", "-map", "0:v:0", "-map", "[aout]"])
    cmd.extend([
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-metadata", f"comment={MONTAGE_ENGINE_V2}",
        "-t", f"{voice_s:.3f}",
        str(out),
    ])
    await _run(cmd, context="mux")


async def run_variant2(
    project: Project,
    frame_numbers: list[int],
    voice: Path,
    out: Path,
    *,
    bgm: BgmConfig | None = None,
) -> Path:
    if not voice.is_file():
        raise RuntimeError(f"нет озвучки: {voice}")

    wipe_montage_workspace(project)
    final_dir = project.data_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    markers, ts_row = load_r15_markers(project, frame_numbers)
    voice_s = await probe_duration(voice)
    marker_end = markers[-1].end_s
    gap = voice_s - marker_end
    if gap > 1.0:
        logger.info(
            "[#{}] R15 до {:.1f}s, озвучка {:.1f}s — последний кадр продлится clone {:.1f}s",
            project.id,
            marker_end,
            voice_s,
            gap,
        )

    write_r15_proof(markers, final_dir / "r15_read.txt", ts_row=ts_row, voice_s=voice_s)

    slots = _all_slots(project, markers)
    src_durations: dict[Path, float] = {}
    for slot in slots:
        if slot.clip not in src_durations:
            src_durations[slot.clip] = await probe_duration(slot.clip)
    plans = build_timeline_segments(slots, src_durations, voice_s)

    w, h = DEFAULT_W, DEFAULT_H
    try:
        w, h = await probe_video_size(slots[0].clip)
    except Exception:  # noqa: BLE001
        pass

    _write_plan(
        plans,
        final_dir / "variant2_plan.txt",
        voice_s=voice_s,
        marker_count=len(markers),
    )

    xlsx = project.data_dir / "project.xlsx"
    st = xlsx.stat()
    (final_dir / "MONTAGE_STAMP.txt").write_text(
        "\n".join([
            f"engine={MONTAGE_ENGINE_V2}",
            "variant=2-overlay-extend",
            f"at={datetime.now(timezone.utc).isoformat()}",
            f"xlsx={xlsx}",
            f"xlsx_mtime={datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()}",
            f"overlay_slots={len(plans)}",
            f"markers={len(markers)}",
            f"voice_s={voice_s:.3f}",
            f"last_marker_end={marker_end:.3f}",
        ]) + "\n",
        encoding="utf-8",
    )

    logger.info(
        "[#{}] overlay: {} slots, voice {:.1f}s, {}x{}",
        project.id,
        len(plans),
        voice_s,
        w,
        h,
    )

    with tempfile.TemporaryDirectory(prefix="vp_montage_overlay_") as td:
        tmp = Path(td)
        video = await _build_overlay_timeline(
            project, plans, w=w, h=h, voice_s=voice_s, tmp=tmp
        )
        pre_mux = final_dir / "_variant2_pre_mux.mp4"
        shutil.copy2(video, pre_mux)
        logger.info("[#{}] overlay timeline → {} (перед mux)", project.id, pre_mux)
        out.parent.mkdir(parents=True, exist_ok=True)
        await _mux(video, voice, out, voice_s=voice_s, bgm=bgm)

    logger.info("[#{}] overlay done → {}", project.id, out)
    return out


def _duration_up_to_frame(seconds: float, *, fps: int = 30) -> float:
    if seconds <= 0.0:
        return 0.0
    return math.ceil(seconds * fps - 1e-9) / fps