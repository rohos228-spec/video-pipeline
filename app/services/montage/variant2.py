"""Вариант 3: R15 → slot encode → concat → mux.

gap_policy=absolute_r15_real_src_reverse_fill

Правила:
1. Старт клипа = Excel r15_start (привязка к таймкоду).
2. Длина на таймлайне = до старта следующего кадра (или конца voice), НЕ обрезка
   по r15_end: пока в файле есть кадры — играем реальный src.
3. Reverse с конца — ТОЛЬКО если реальный src короче времени до следующего старта.
4. Нельзя решать «видео кончилось», потому что кончился таймкод Excel.
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

MONTAGE_ENGINE_V2 = "montage-v3-r15-slots-concat-s2"
GAP_POLICY = "absolute_r15_real_src_reverse_fill"
DEFAULT_W, DEFAULT_H = 1920, 1080
SLOT_ENCODE_PARALLEL = 4
_TIMELINE_FPS = 30
_DEFAULT_VOICE_GAIN = 1.0
_DEFAULT_BGM_MIX_RATIO = 0.35
_R15_ALIGN_TOL_S = 0.03

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
    """Сегмент на абсолютной шкале. slot_dur = длина на таймлайне (окно или gap)."""

    frame_number: int
    clip: Path
    kind: str
    label: str
    out_start: float
    out_end: float
    r15_start: float
    r15_end: float
    slot_dur: float
    src_dur: float
    start_phase: str = "fwd"  # fwd | rev — с чего начать ping-pong
    bound_to_r15: bool = True  # False для lead/gap/tail доборов
    prefix_pad: float = 0.0
    suffix_pad: float = 0.0

    @property
    def out_duration(self) -> float:
        return self.slot_dur


@dataclass(frozen=True)
class _TimelineSegment:
    kind: str  # clip
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
            "[#{}] variant3: кадры {} без clip — дыра reverse-fill соседним",
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


def _pingpong_plan(
    src_dur: float,
    out_dur: float,
    *,
    start_phase: str = "fwd",
) -> list[tuple[str, float]]:
    """План кусков: сначала вперёд, если не хватает — reverse с конца, потом снова вперёд…"""
    src_dur = max(0.05, src_dur)
    out_dur = max(0.05, out_dur)
    if out_dur <= src_dur + 0.02 and start_phase == "fwd":
        return [("fwd", out_dur)]
    if out_dur <= src_dur + 0.02 and start_phase == "rev":
        return [("rev", out_dur)]

    parts: list[tuple[str, float]] = []
    rem = out_dur
    phase = start_phase if start_phase in ("fwd", "rev") else "fwd"
    # Первая фаза fwd при нехватке — полный прогон клипа, затем reverse.
    if phase == "fwd":
        chunk = min(src_dur, rem)
        parts.append(("fwd", chunk))
        rem -= chunk
        phase = "rev"
    while rem > 0.02:
        chunk = min(src_dur, rem)
        parts.append((phase, chunk))
        rem -= chunk
        phase = "fwd" if phase == "rev" else "rev"
    return parts


def _fill_segment(
    *,
    frame_number: int,
    clip: Path,
    kind: str,
    label: str,
    out_start: float,
    duration: float,
    src_dur: float,
    r15_start: float,
    r15_end: float,
    start_phase: str,
    bound_to_r15: bool,
) -> _TimelineSegment:
    cs = _ContinuousSlot(
        frame_number=frame_number,
        clip=clip,
        kind=kind,
        label=label,
        out_start=out_start,
        out_end=out_start + duration,
        r15_start=r15_start,
        r15_end=r15_end,
        slot_dur=duration,
        src_dur=src_dur,
        start_phase=start_phase,
        bound_to_r15=bound_to_r15,
    )
    return _TimelineSegment("clip", duration, cs)


def build_timeline_segments(
    slots: list[_OverlaySlot],
    src_durations: dict[Path, float],
    voice_s: float,
) -> list[_TimelineSegment]:
    """Старт по R15; длина от реального src до следующего старта; reverse лишь если src мал."""
    ordered = sorted(slots, key=lambda s: (s.start_s, s.frame_number))
    segs: list[_TimelineSegment] = []
    cursor = 0.0

    for i, slot in enumerate(ordered):
        src_dur = src_durations[slot.clip]
        next_start = ordered[i + 1].start_s if i + 1 < len(ordered) else voice_s
        # Сколько места до ОБЯЗАТЕЛЬНОГО старта следующего кадра / конца voice.
        available = next_start - slot.start_s
        if available < 0.05:
            logger.warning(
                "variant3: кадр {} — нет места до следующего старта ({:.2f}→{:.2f}), пропуск",
                slot.frame_number,
                slot.start_s,
                next_start,
            )
            continue

        # Lead до первого r15_start — reverse-fill первым клипом.
        if slot.start_s > cursor + 0.02:
            gap = slot.start_s - cursor
            segs.append(
                _fill_segment(
                    frame_number=slot.frame_number,
                    clip=slot.clip,
                    kind=slot.kind,
                    label=slot.label,
                    out_start=cursor,
                    duration=gap,
                    src_dur=src_dur,
                    r15_start=slot.start_s,
                    r15_end=slot.end_s,
                    start_phase="rev",
                    bound_to_r15=False,
                )
            )
            cursor = slot.start_s
        elif slot.start_s < cursor - _R15_ALIGN_TOL_S:
            raise RuntimeError(
                f"кадр {slot.frame_number}: R15 {slot.start_s:.2f} < cursor {cursor:.2f}"
            )
        else:
            cursor = max(cursor, slot.start_s)

        if abs(cursor - slot.start_s) > _R15_ALIGN_TOL_S:
            raise RuntimeError(
                f"кадр {slot.frame_number}: отвязка R15 "
                f"(cursor={cursor:.3f}, r15_start={slot.start_s:.3f})"
            )

        # КРИТИЧНО: не режем по r15_end. Режем только по available / реальному src.
        # Reverse — только если ФАЙЛ короче available, не потому что «таймкод окна кончился».
        r15_win = max(0.0, slot.end_s - slot.start_s)
        if src_dur + 0.02 < available:
            logger.debug(
                "variant3: кадр {} — реальный src {:.2f}s < места {:.2f}s "
                "(R15-окно было {:.2f}s) → fwd + reverse",
                slot.frame_number,
                src_dur,
                available,
                r15_win,
            )
        elif src_dur > r15_win + 0.05 and src_dur <= available + 0.02:
            logger.debug(
                "variant3: кадр {} — src {:.2f}s > R15-окно {:.2f}s, "
                "но файл ещё идёт → играем src до {:.2f}s (не режем по таймкоду конца)",
                slot.frame_number,
                src_dur,
                r15_win,
                available,
            )

        seg = _fill_segment(
            frame_number=slot.frame_number,
            clip=slot.clip,
            kind=slot.kind,
            label=slot.label,
            out_start=slot.start_s,
            duration=available,
            src_dur=src_dur,
            r15_start=slot.start_s,
            r15_end=slot.end_s,
            start_phase="fwd",
            bound_to_r15=True,
        )
        segs.append(seg)
        cursor = next_start

    if voice_s > cursor + 0.02 and segs:
        last = segs[-1].slot
        assert last is not None
        segs.append(
            _fill_segment(
                frame_number=last.frame_number,
                clip=last.clip,
                kind=last.kind,
                label=last.label,
                out_start=cursor,
                duration=voice_s - cursor,
                src_dur=last.src_dur,
                r15_start=last.r15_start,
                r15_end=last.r15_end,
                start_phase="rev",
                bound_to_r15=False,
            )
        )

    _validate_timeline(segs, voice_s, ordered_starts=[s.start_s for s in ordered])
    return segs


def _validate_timeline(
    segments: list[_TimelineSegment],
    voice_s: float,
    *,
    ordered_starts: list[float] | None = None,
) -> None:
    cursor = 0.0
    bound_i = 0
    for seg in segments:
        if seg.kind != "clip" or seg.slot is None:
            raise RuntimeError("ожидался clip-сегмент")
        cs = seg.slot
        if abs(cs.prefix_pad) > 0.001 or abs(cs.suffix_pad) > 0.001:
            raise RuntimeError(f"кадр {cs.frame_number}: freeze-pad запрещён")
        if abs(cs.out_start - cursor) > _R15_ALIGN_TOL_S:
            raise RuntimeError(
                f"кадр {cs.frame_number}: разрыв шкалы "
                f"(out_start={cs.out_start:.3f}, cursor={cursor:.3f})"
            )
        if cs.bound_to_r15 and abs(cs.out_start - cs.r15_start) > _R15_ALIGN_TOL_S:
            raise RuntimeError(
                f"кадр {cs.frame_number}: ОТВЯЗКА от R15 "
                f"(out_start={cs.out_start:.3f} != r15_start={cs.r15_start:.3f})"
            )
        # Длина bound-сегмента = до следующего R15-старта, НЕ обязана = r15_end-r15_start.
        if cs.bound_to_r15 and ordered_starts is not None:
            if bound_i + 1 < len(ordered_starts):
                expect = ordered_starts[bound_i + 1] - cs.r15_start
            else:
                expect = voice_s - cs.r15_start
            if abs(cs.slot_dur - expect) > 0.05:
                raise RuntimeError(
                    f"кадр {cs.frame_number}: длина {cs.slot_dur:.2f} "
                    f"!= места до следующего старта {expect:.2f}"
                )
            bound_i += 1
        if abs(seg.duration_s - cs.out_duration) > 0.03:
            raise RuntimeError(f"кадр {cs.frame_number}: duration mismatch")
        cursor += seg.duration_s
    if abs(cursor - voice_s) > 0.05:
        raise RuntimeError(f"timeline {cursor:.2f}s != voice {voice_s:.2f}s")


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


def _base_vf(w: int, h: int) -> str:
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
    )


def _clip_filter_chain(
    w: int,
    h: int,
    slot_dur: float,
    src_dur: float,
    *,
    prefix_pad: float = 0.0,
    suffix_pad: float = 0.0,
) -> str:
    """Простой trim (когда src хватает). Без tpad/slow-mo."""
    if prefix_pad > 0.001 or suffix_pad > 0.001:
        raise RuntimeError("prefix/suffix freeze-pad запрещены")
    use = max(0.05, min(slot_dur, src_dur if src_dur > 0 else slot_dur))
    return f"{_base_vf(w, h)},trim=duration={use:.3f},setpts=PTS-STARTPTS"


def _needs_reverse_fill(src_dur: float, out_dur: float, start_phase: str) -> bool:
    if start_phase == "rev":
        return True
    return src_dur + 0.02 < out_dur


def _build_pingpong_filter(
    w: int,
    h: int,
    src_dur: float,
    out_dur: float,
    *,
    start_phase: str,
) -> str:
    parts = _pingpong_plan(src_dur, out_dur, start_phase=start_phase)
    n = len(parts)
    base = _base_vf(w, h)
    if n == 1 and parts[0][0] == "fwd":
        return f"{base},trim=duration={parts[0][1]:.3f},setpts=PTS-STARTPTS"
    if n == 1 and parts[0][0] == "rev":
        d = parts[0][1]
        return (
            f"{base},trim=duration={src_dur:.3f},setpts=PTS-STARTPTS,"
            f"reverse,trim=duration={d:.3f},setpts=PTS-STARTPTS"
        )

    # filter_complex через -filter_complex в encode; здесь вернём маркер —
    # реально собираем в _encode_clip_segment.
    raise RuntimeError("use _encode_pingpong_segment for multi-part")


async def _encode_pingpong_segment(
    slot: _ContinuousSlot,
    path: Path,
    *,
    w: int,
    h: int,
) -> None:
    parts = _pingpong_plan(
        slot.src_dur, slot.slot_dur, start_phase=slot.start_phase
    )
    src_dur = max(0.05, slot.src_dur)
    base = _base_vf(w, h)

    if len(parts) == 1:
        phase, dur = parts[0]
        if phase == "fwd":
            vf = f"{base},trim=duration={dur:.3f},setpts=PTS-STARTPTS"
        else:
            vf = (
                f"{base},trim=duration={src_dur:.3f},setpts=PTS-STARTPTS,"
                f"reverse,trim=duration={dur:.3f},setpts=PTS-STARTPTS"
            )
        await _run([
            "ffmpeg", "-y",
            "-i", str(slot.clip),
            "-vf", vf,
            *_X264,
            "-an",
            "-t", f"{slot.out_duration:.3f}",
            str(path),
        ], context=f"pingpong f{slot.frame_number} {phase} {dur:.2f}s")
        return

    n = len(parts)
    splits = "".join(f"[s{i}]" for i in range(n))
    fc_parts = [f"[0:v]{base},split={n}{splits}"]
    outs: list[str] = []
    for i, (phase, dur) in enumerate(parts):
        label = f"p{i}"
        outs.append(f"[{label}]")
        if phase == "fwd":
            fc_parts.append(
                f"[s{i}]trim=duration={dur:.3f},setpts=PTS-STARTPTS[{label}]"
            )
        else:
            fc_parts.append(
                f"[s{i}]trim=duration={src_dur:.3f},setpts=PTS-STARTPTS,"
                f"reverse,trim=duration={dur:.3f},setpts=PTS-STARTPTS[{label}]"
            )
    concat_in = "".join(outs)
    fc_parts.append(
        f"{concat_in}concat=n={n}:v=1:a=0,"
        f"trim=duration={slot.slot_dur:.3f},setpts=PTS-STARTPTS[vout]"
    )
    fc = ";".join(fc_parts)
    await _run([
        "ffmpeg", "-y",
        "-i", str(slot.clip),
        "-filter_complex", fc,
        "-map", "[vout]",
        *_X264,
        "-an",
        "-t", f"{slot.out_duration:.3f}",
        str(path),
    ], context=f"pingpong f{slot.frame_number} {n} parts {slot.slot_dur:.2f}s")


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
        f"gap_policy={GAP_POLICY}",
        "short_src_policy=forward_then_reverse",
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
        cs = seg.slot
        assert cs is not None
        out_total += seg.duration_s
        bound = "r15" if cs.bound_to_r15 else "fill"
        lines.append(
            f"{cs.frame_number}\t{cs.kind}\t{cs.label}\t{cs.r15_start:.3f}\t{cs.r15_end:.3f}\t"
            f"{cs.out_start:.3f}\t{cs.out_end:.3f}\t{seg.duration_s:.3f}\t{cs.clip.name}"
            f"\tsrc={cs.src_dur:.2f}\tphase={cs.start_phase}\t{bound}"
        )
    lines.append(f"\nclip_slots_total={clip_total:.3f}")
    lines.append(f"timeline_out_total={out_total:.3f}")
    lines.append(f"segments={len(segments)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _duration_up_to_frame(seconds: float, *, fps: int = _TIMELINE_FPS) -> float:
    if seconds <= 0.0:
        return 0.0
    return math.ceil(seconds * fps - 1e-9) / fps


async def _concat_segments(list_file: Path, out: Path, *, voice_s: float) -> None:
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
        logger.warning("variant3: concat copy failed — re-encode once")
        await _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            *_X264,
            "-an",
            "-t", f"{voice_s:.3f}",
            str(out),
        ], context="concat re-encode")


async def _ensure_timeline_duration(
    src: Path,
    dst: Path,
    *,
    w: int,
    h: int,
    voice_s: float,
    tmp: Path,
    project_id: int,
) -> Path:
    got = await probe_duration(src)
    if abs(got - voice_s) <= 0.05:
        if src != dst:
            shutil.copy2(src, dst)
        return dst

    if got + 0.05 < voice_s:
        # Не должно случаться при корректном plan; добьём reverse последнего сегмента нельзя тут —
        # обрежем ошибку явно trim/pad через повторный encode reverse от src (как motion).
        pad_s = _duration_up_to_frame(voice_s - got)
        logger.warning(
            "[#{}] variant3: timeline {:.2f}s < voice {:.2f}s — reverse-pad {:.3f}s",
            project_id,
            got,
            voice_s,
            pad_s,
        )
        pad_path = tmp / "pad_rev.mp4"
        # reverse последних кадров всего timeline
        await _run([
            "ffmpeg", "-y",
            "-i", str(src),
            "-vf",
            (
                f"{_base_vf(w, h)},reverse,trim=duration={pad_s:.3f},setpts=PTS-STARTPTS"
            ),
            *_X264,
            "-an",
            "-t", f"{pad_s:.3f}",
            str(pad_path),
        ], context=f"reverse-pad {pad_s:.2f}s")
        list_file = tmp / "concat_padded.txt"
        list_file.write_text(
            "\n".join((f"file '{src.as_posix()}'", f"file '{pad_path.as_posix()}'")),
            encoding="utf-8",
        )
        padded = tmp / "timeline_padded.mp4"
        await _concat_segments(list_file, padded, voice_s=voice_s)
        src = padded
        got = await probe_duration(src)

    if got > voice_s + 0.05:
        await _run([
            "ffmpeg", "-y",
            "-i", str(src),
            "-vf", f"trim=duration={voice_s:.6f},setpts=PTS-STARTPTS",
            *_X264,
            "-an",
            "-t", f"{voice_s:.3f}",
            str(dst),
        ], context=f"trim timeline to {voice_s:.2f}s")
        return dst

    if src != dst:
        shutil.copy2(src, dst)
    return dst


async def _encode_clip_segment(
    slot: _ContinuousSlot,
    path: Path,
    *,
    w: int,
    h: int,
) -> None:
    if _needs_reverse_fill(slot.src_dur, slot.slot_dur, slot.start_phase):
        await _encode_pingpong_segment(slot, path, w=w, h=h)
        return
    vf = _clip_filter_chain(w, h, slot.slot_dur, slot.src_dur)
    await _run([
        "ffmpeg", "-y",
        "-i", str(slot.clip),
        "-vf", vf,
        *_X264,
        "-an",
        "-t", f"{slot.out_duration:.3f}",
        str(path),
    ], context=f"clip slot f{slot.frame_number} {slot.out_duration:.2f}s")


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
    raw = tmp / "timeline_raw.mp4"
    await _concat_segments(list_file, raw, voice_s=voice_s)
    out = tmp / "timeline.mp4"
    await _ensure_timeline_duration(
        raw,
        out,
        w=w,
        h=h,
        voice_s=voice_s,
        tmp=tmp,
        project_id=project.id,
    )

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
            "[#{}] R15 до {:.1f}s, озвучка {:.1f}s — хвост {:.1f}s reverse-fill",
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
            f"gap_policy={GAP_POLICY}",
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
