"""Вариант 3 (быстрый): R15 → slot-файлы (gap + clip) → concat → mux.

Каждый сегмент кодируется только на свою длительность (2–5 с), а не на всю
шкалу озвучки (~500 с). Для 140+ клипов это ~5–8 мин вместо ~20 мин overlay.
"""

from __future__ import annotations

import asyncio
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

MONTAGE_ENGINE_V2 = "montage-v3-r15-slots-concat-s2"
DEFAULT_W, DEFAULT_H = 1920, 1080
SLOT_ENCODE_PARALLEL = 4
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
class _ContinuousSlot:
    """Клип на плотной шкале concat: без freeze последнего кадра."""

    frame_number: int
    clip: Path
    kind: str
    label: str
    out_start: float
    out_end: float
    r15_start: float
    r15_end: float
    play_dur: float
    src_dur: float

    @property
    def out_duration(self) -> float:
        return self.play_dur


@dataclass(frozen=True)
class _TimelineSegment:
    kind: str  # clip | black
    duration_s: float
    slot: _ContinuousSlot | None = None


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
            "[#{}] variant3: кадры {} без clip — пропуск, след. клип сразу после предыдущего",
            project.id,
            skipped,
        )
    if not slots:
        raise RuntimeError("нет ни одного videos/clip_*.mp4 для монтажа")
    if split_n:
        logger.info(
            "[#{}] variant2 shot2: {} кадров 50/50 ({} overlay-слотов)",
            project.id,
            split_n,
            len(slots),
        )
    return slots


def _play_duration(r15_window: float, src_dur: float) -> float:
    """Длительность клипа на таймлайне: min(R15, src), без freeze."""
    return max(0.0, min(r15_window, src_dur))


def build_continuous_slots(
    slots: list[_OverlaySlot],
    src_durations: dict[Path, float],
) -> tuple[list[_ContinuousSlot], float]:
    """Плотный concat: каждый клип = естественная длина (≤ R15), сразу следующий."""
    ordered = sorted(slots, key=lambda s: (s.start_s, s.frame_number))
    out: list[_ContinuousSlot] = []
    cursor = 0.0
    for slot in ordered:
        src_dur = src_durations[slot.clip]
        play = _play_duration(slot.duration_s, src_dur)
        if play < 0.05:
            logger.warning(
                "variant3: кадр {} — клип {:.2f}s короче 0.05s, пропуск",
                slot.frame_number,
                src_dur,
            )
            continue
        if src_dur + 0.05 < slot.duration_s:
            logger.debug(
                "variant3: кадр {} — src {:.2f}s < R15 {:.2f}s, без freeze → след. клип",
                slot.frame_number,
                src_dur,
                slot.duration_s,
            )
        out_start = cursor
        out_end = cursor + play
        out.append(
            _ContinuousSlot(
                frame_number=slot.frame_number,
                clip=slot.clip,
                kind=slot.kind,
                label=slot.label,
                out_start=out_start,
                out_end=out_end,
                r15_start=slot.start_s,
                r15_end=slot.end_s,
                play_dur=play,
                src_dur=src_dur,
            )
        )
        cursor = out_end
    return out, cursor


def build_timeline_segments(
    slots: list[_OverlaySlot],
    src_durations: dict[Path, float],
    voice_s: float,
) -> list[_TimelineSegment]:
    """Клипы подряд без freeze; хвост озвучки — только чёрный filler (не freeze клипа)."""
    continuous, video_total = build_continuous_slots(slots, src_durations)
    segs = [
        _TimelineSegment("clip", cs.play_dur, cs)
        for cs in continuous
    ]
    tail = voice_s - video_total
    if tail > 0.02:
        segs.append(_TimelineSegment("black", tail))
    return segs


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


def _clip_filter_chain(w: int, h: int, play_dur: float, src_dur: float) -> str:
    """Обрезка по min(R15, src). Без tpad/freeze последнего кадра."""
    use = min(play_dur, src_dur)
    chain = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
    )
    if src_dur > use + 0.05:
        chain += f",trim=duration={use:.3f}"
    chain += f",setpts=PTS-STARTPTS"
    return chain


def _write_plan(
    slots: list[_OverlaySlot],
    segments: list[_TimelineSegment],
    path: Path,
    *,
    voice_s: float,
    marker_count: int,
) -> None:
    lines = [
        f"engine={MONTAGE_ENGINE_V2}",
        f"voice_duration={voice_s:.3f}",
        f"markers={marker_count}",
        f"overlay_slots={len(slots)}",
        f"timeline_segments={len(segments)}",
        "gap_policy=dense_natural_no_freeze",
        "",
        "frame\tkind\texcel\tr15_start\tr15_end\tout_start\tout_end\tdur\tclip",
    ]
    clip_total = 0.0
    out_total = 0.0
    for s in slots:
        clip_total += s.duration_s
        lines.append(
            f"{s.frame_number}\t{s.kind}\t{s.label}\t{s.start_s:.3f}\t{s.end_s:.3f}\t"
            f"\t\t{s.duration_s:.3f}\t{s.clip.name}"
        )
    for seg in segments:
        if seg.kind == "black":
            out_total += seg.duration_s
            lines.append(f"—\tblack\tvoice_tail\t\t\t\t\t{seg.duration_s:.3f}\t—")
            continue
        cs = seg.slot
        assert cs is not None
        out_total += seg.duration_s
        lines.append(
            f"{cs.frame_number}\t{cs.kind}\t{cs.label}\t{cs.r15_start:.3f}\t{cs.r15_end:.3f}\t"
            f"{cs.out_start:.3f}\t{cs.out_end:.3f}\t{seg.duration_s:.3f}\t{cs.clip.name}"
            f"\tsrc={cs.src_dur:.2f}"
        )
    lines.append(f"\nclip_slots_total={clip_total:.3f}")
    lines.append(f"timeline_out_total={out_total:.3f}")
    lines.append(f"segments={len(segments)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _encode_black_segment(path: Path, *, w: int, h: int, dur: float) -> None:
    await _run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s={w}x{h}:d={dur:.3f}:r=30",
        *_X264,
        "-an",
        "-t", f"{dur:.3f}",
        str(path),
    ], context=f"black tail {dur:.2f}s")


async def _encode_clip_segment(
    slot: _ContinuousSlot,
    path: Path,
    *,
    w: int,
    h: int,
) -> None:
    vf = _clip_filter_chain(w, h, slot.play_dur, slot.src_dur)
    await _run([
        "ffmpeg", "-y",
        "-i", str(slot.clip),
        "-vf", vf,
        *_X264,
        "-an",
        "-t", f"{slot.play_dur:.3f}",
        str(path),
    ], context=f"clip slot f{slot.frame_number} {slot.play_dur:.2f}s")


async def _build_slot_timeline(
    project: Project,
    segments: list[_TimelineSegment],
    *,
    w: int,
    h: int,
    voice_s: float,
    tmp: Path,
) -> Path:
    sem = asyncio.Semaphore(SLOT_ENCODE_PARALLEL)
    done = 0
    total = len(segments)
    paths: list[Path | None] = [None] * total

    async def _one(idx: int, seg: _TimelineSegment) -> None:
        nonlocal done
        out = tmp / f"seg_{idx:04d}.mp4"
        async with sem:
            if seg.kind == "black":
                await _encode_black_segment(out, w=w, h=h, dur=seg.duration_s)
            else:
                assert seg.slot is not None
                await _encode_clip_segment(seg.slot, out, w=w, h=h)
        paths[idx] = out
        done += 1
        if done % 20 == 0 or done == total:
            logger.info(
                "[#{}] variant3 slots encoded {}/{}",
                project.id,
                done,
                total,
            )

    await asyncio.gather(*(_one(i, seg) for i, seg in enumerate(segments)))

    list_file = tmp / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in paths if p is not None),
        encoding="utf-8",
    )
    out = tmp / "timeline.mp4"
    try:
        await _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            "-an",
            "-t", f"{voice_s:.3f}",
            str(out),
        ], context="concat copy")
    except RuntimeError:
        logger.warning("[#{}] variant3: concat copy failed — re-encode once", project.id)
        await _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            *_X264,
            "-an",
            "-t", f"{voice_s:.3f}",
            str(out),
        ], context="concat re-encode")

    got = await probe_duration(out)
    if abs(got - voice_s) > 0.5:
        raise RuntimeError(f"slot timeline {got:.2f}s != voice {voice_s:.2f}s")
    return out


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
            "[#{}] R15 до {:.1f}s, озвучка {:.1f}s — хвост {:.1f}s = чёрный filler (не freeze клипа)",
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
    segments = build_timeline_segments(slots, src_durations, voice_s)

    w, h = DEFAULT_W, DEFAULT_H
    try:
        w, h = await probe_video_size(slots[0].clip)
    except Exception:  # noqa: BLE001
        pass

    _write_plan(
        slots,
        segments,
        final_dir / "variant2_plan.txt",
        voice_s=voice_s,
        marker_count=len(markers),
    )

    xlsx = project.data_dir / "project.xlsx"
    st = xlsx.stat()
    (final_dir / "MONTAGE_STAMP.txt").write_text(
        "\n".join([
            f"engine={MONTAGE_ENGINE_V2}",
            "variant=3-slots",
            f"at={datetime.now(timezone.utc).isoformat()}",
            f"xlsx={xlsx}",
            f"xlsx_mtime={datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()}",
            f"overlay_slots={len(slots)}",
            f"timeline_segments={len(segments)}",
            f"markers={len(markers)}",
            f"voice_s={voice_s:.3f}",
            f"last_marker_end={marker_end:.3f}",
        ]) + "\n",
        encoding="utf-8",
    )

    logger.info(
        "[#{}] variant3: {} slots → {} segments, voice {:.1f}s, {}x{}",
        project.id,
        len(slots),
        len(segments),
        voice_s,
        w,
        h,
    )

    with tempfile.TemporaryDirectory(prefix="vp_montage_v3_") as td:
        tmp = Path(td)
        video = await _build_slot_timeline(
            project, segments, w=w, h=h, voice_s=voice_s, tmp=tmp
        )
        pre_mux = final_dir / "_variant2_pre_mux.mp4"
        shutil.copy2(video, pre_mux)
        logger.info("[#{}] variant3: timeline сохранён → {} (перед mux)", project.id, pre_mux)
        out.parent.mkdir(parents=True, exist_ok=True)
        await _mux(video, voice, out, voice_s=voice_s, bgm=bgm)
        if pre_mux.is_file():
            pre_mux.unlink(missing_ok=True)

    logger.info("[#{}] variant3 done → {}", project.id, out)
    return out
