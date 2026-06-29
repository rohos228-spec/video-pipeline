"""FFmpeg-сборка финального ролика.

Вход:
  - список (clip_path: mp4, duration: float) в порядке кадров,
  - путь к единой mp3-озвучке,
  - путь выходного mp4.

Что делаем:
  1. Обрезка каждого клипа по длительности без смены разрешения и FPS.
  2. Склейка concat (все клипы должны быть одного размера — как у Outsee).
  3. Наложение mp3.
  4. Опционально ASS-субтитры под фактическое разрешение ролика.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.services.bgm import BgmConfig
from app.services.media_probe import probe_duration, probe_video_size
from app.settings import settings

SUBTITLES_ASS_NAME = "subs.ass"
# запасной размер ASS, если ffprobe недоступен (16:9)
DEFAULT_ASS_W, DEFAULT_ASS_H = 1920, 1080


def subtitles_vf_arg(filename: str = SUBTITLES_ASS_NAME) -> str:
    """Return -vf value for burning ASS subtitles from cwd=temp dir."""
    return f"subtitles={filename}"


def subtitle_layout(width: int, height: int) -> tuple[int, int, str]:
    """Центр снизу: позиция субтитров под фактическое разрешение кадра."""
    x = width // 2
    base_y = int(height * 0.92)
    y = int(base_y - height * 0.15)
    prefix = rf"{{\an2\pos({x},{y})}}"
    return x, y, prefix


@dataclass
class ClipSpec:
    duration: float  # требуемая длительность после обрезки
    src: Path | None = None
    frame_number: int | None = None
    timeline_start: float | None = None
    timeline_end: float | None = None
    kind: str = "scene"  # scene | shot2 | black


async def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    context: str = "",
) -> None:
    logger.debug("$ {}", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd is not None else None,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="ignore").strip()
        rc = proc.returncode
        signed = rc - (1 << 32) if rc is not None and rc > 2**31 - 1 else rc
        head = f"ffmpeg exit {rc}"
        if signed is not None and signed != rc:
            head += f" ({signed})"
        if context:
            head += f" — {context}"
        tail = "\n".join(err.splitlines()[-20:])
        logger.error("{}\n{}", head, err)
        raise RuntimeError(f"{head}\n{tail}")


async def _cut_clip(
    src: Path,
    duration: float,
    dst: Path,
    *,
    target_w: int | None = None,
    target_h: int | None = None,
) -> None:
    """Обрезка по времени; при несовпадении размера — scale+pad под эталон."""
    if not src.is_file():
        raise FileNotFoundError(f"clip not found: {src}")

    need = max(float(duration), 0.05)
    try:
        src_dur = await probe_duration(src)
    except RuntimeError as exc:
        raise RuntimeError(f"ffprobe failed for {src.name}: {exc}") from exc

    cmd: list[str] = ["ffmpeg", "-y"]
    # Монтаж может требовать дольше, чем длится исходник — держим последний кадр.
    if need > src_dur + 0.05:
        logger.debug(
            "assembly: {} {:.2f}s needed, file {:.2f}s — stream_loop",
            src.name,
            need,
            src_dur,
        )
        cmd.extend(["-stream_loop", "-1"])
    cmd.extend(["-i", str(src), "-t", f"{need:.3f}"])
    if target_w is not None and target_h is not None:
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        cmd.extend(["-vf", vf])
    cmd.extend([
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
        str(dst),
    ])
    await _run(cmd, context=f"cut {src.name} → {need:.2f}s")


async def _resolve_assembly_target_size(clips: list[ClipSpec]) -> tuple[int, int]:
    """Эталон = первый реальный клип; остальные подгоняем."""
    for spec in clips:
        if spec.src is None or not spec.src.is_file():
            continue
        target_w, target_h = await probe_video_size(spec.src)
        for other in clips:
            if other.src is None or not other.src.is_file():
                continue
            w2, h2 = await probe_video_size(other.src)
            if (w2, h2) != (target_w, target_h):
                logger.warning(
                    "assembly: {} {}x{} → нормализация под {}x{}",
                    other.src.name,
                    w2,
                    h2,
                    target_w,
                    target_h,
                )
        logger.info("assembly: целевое видео {}x{}", target_w, target_h)
        return target_w, target_h
    logger.info("assembly: нет клипов — fallback {}x{}", DEFAULT_ASS_W, DEFAULT_ASS_H)
    return DEFAULT_ASS_W, DEFAULT_ASS_H


import re

ASSEMBLY_ENGINE = "excel-r15-markers-v13"


def write_marker_proof(
    specs: list[ClipSpec],
    ts_cells: list[tuple[int, str]],
    path: Path,
) -> None:
    """Таблица: что именно ушло в ffmpeg — для сверки с Excel."""
    labels = dict(ts_cells)
    lines = [
        f"engine={ASSEMBLY_ENGINE}",
        "frame\texcel_r15\tstart_s\tend_s\tclip",
        "",
    ]
    for spec in specs:
        if spec.kind not in ("scene", "shot2") or spec.timeline_start is None:
            continue
        num = spec.frame_number or 0
        clip_name = spec.src.name if spec.src else "BLACK"
        lines.append(
            f"{num}\t{labels.get(num, '')}\t"
            f"{spec.timeline_start:.3f}\t{spec.timeline_end:.3f}\t{clip_name}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_concat_plan(clips: list[ClipSpec], path: Path) -> None:
    lines = [f"engine={ASSEMBLY_ENGINE}", f"segments={len(clips)}", ""]
    for i, spec in enumerate(clips):
        t0 = spec.timeline_start if spec.timeline_start is not None else 0.0
        t1 = spec.timeline_end if spec.timeline_end is not None else t0 + spec.duration
        lines.append(
            f"{i:03d}\t{spec.kind}\tframe={spec.frame_number or '-'}"
            f"\tABS {t0:.3f}-{t1:.3f}s\t{spec.duration:.3f}s\t{spec.src.name}"
        )
    if clips:
        ends = [
            s.timeline_end if s.timeline_end is not None else (s.timeline_start or 0) + s.duration
            for s in clips
        ]
        lines.append(f"\nlast_frame_end={max(ends):.3f}s")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _make_black_base(
    path: Path,
    *,
    width: int,
    height: int,
    duration: float,
) -> None:
    await _run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s={width}x{height}:d={max(duration, 0.05):.3f}:r=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
        "-an",
        str(path),
    ])


async def _make_black_segment(
    path: Path,
    *,
    width: int,
    height: int,
    duration: float,
) -> None:
    await _make_black_base(path, width=width, height=height, duration=duration)


async def _render_absolute_concat_timeline(
    layers: list[ClipSpec],
    *,
    width: int,
    height: int,
    mux_t: float,
    tmp_dir: Path,
) -> Path:
    """R15 → шкала: пауза до start, клип [start,end], порядок как в specs."""
    segment_paths: list[Path] = []
    pos = 0.0

    for spec in layers:
        t0 = float(spec.timeline_start or 0.0)
        t1 = float(spec.timeline_end or (t0 + spec.duration))
        dur = max(t1 - t0, 0.05)

        if spec.frame_number is not None and spec.src is not None:
            m = re.match(r"clip_(\d+)_", spec.src.name, re.I)
            if m and int(m.group(1)) != spec.frame_number:
                raise RuntimeError(
                    f"кадр {spec.frame_number}: файл {spec.src.name} — другой номер"
                )

        if t0 > pos + 0.02:
            gap_path = tmp_dir / f"gap_{len(segment_paths):04d}.mp4"
            await _make_black_segment(
                gap_path,
                width=width,
                height=height,
                duration=t0 - pos,
            )
            segment_paths.append(gap_path)
            pos = t0

        if t0 < pos - 0.02:
            raise RuntimeError(
                f"кадр {spec.frame_number}: start {t0:.3f}s < позиция {pos:.3f}s "
                f"(метки R15 перекрываются?)"
            )

        scene_path = tmp_dir / f"scene_{spec.frame_number or 0:03d}_{len(segment_paths):04d}.mp4"
        if spec.kind == "black" or spec.src is None or not spec.src.is_file():
            await _make_black_segment(
                scene_path,
                width=width,
                height=height,
                duration=dur,
            )
        else:
            await _cut_clip(
                spec.src,
                dur,
                scene_path,
                target_w=width,
                target_h=height,
            )
        segment_paths.append(scene_path)
        pos = t1

    if mux_t > pos + 0.02:
        tail_path = tmp_dir / "gap_tail.mp4"
        await _make_black_segment(
            tail_path,
            width=width,
            height=height,
            duration=mux_t - pos,
        )
        segment_paths.append(tail_path)
        pos = mux_t

    logger.info(
        "assembly: absolute concat {} segments, span {:.2f}s / mux {:.2f}s",
        len(segment_paths),
        pos,
        mux_t,
    )

    list_file = tmp_dir / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in segment_paths),
        encoding="utf-8",
    )
    concat_mp4 = tmp_dir / "concat.mp4"
    await _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
        "-an",
        "-t", f"{mux_t:.3f}",
        str(concat_mp4),
    ])
    return concat_mp4


async def assemble(
    clips: list[ClipSpec],
    audio_path: Path,
    out_path: Path,
    *,
    subtitles_ass: Path | None = None,
    max_duration: float | None = None,
    bgm: BgmConfig | None = None,
) -> Path:
    if not clips:
        raise ValueError("нет клипов для сборки")

    layers = [
        s for s in clips
        if s.kind in ("scene", "shot2", "black")
        and s.timeline_start is not None
        and s.duration > 0
    ]
    if not layers:
        raise ValueError("нет слоёв с абсолютными метками timeline_start")

    layers.sort(key=lambda s: (float(s.timeline_start or 0), s.frame_number or 0))

    layer_ends = [
        float(s.timeline_end or (float(s.timeline_start or 0) + s.duration))
        for s in layers
    ]
    timeline_end = max(layer_ends)
    voice_t = float(max_duration) if max_duration is not None else timeline_end
    mux_t = max(timeline_end, voice_t)

    logger.info(
        "assembly engine={} layers={} timeline_end={:.2f}s mux_t={:.2f}s",
        ASSEMBLY_ENGINE,
        len(layers),
        timeline_end,
        mux_t,
    )

    for i, spec in enumerate(layers):
        if spec.kind != "black" and spec.src is not None and not spec.src.is_file():
            raise FileNotFoundError(f"layer #{i} missing: {spec.src}")
        t0 = float(spec.timeline_start or 0)
        t1 = float(spec.timeline_end or (t0 + spec.duration))
        if t1 <= t0 + 0.01:
            raise ValueError(
                f"layer #{i} frame {spec.frame_number}: bad window {t0:.2f}-{t1:.2f}s"
            )
        logger.debug(
            "assembly layer frame={} ABS {:.2f}-{:.2f}s {}",
            spec.frame_number,
            t0,
            t1,
            spec.src.name if spec.src else "BLACK",
        )

    target_w, target_h = await _resolve_assembly_target_size(layers)

    with tempfile.TemporaryDirectory(prefix="vp_asm_") as tmp:
        tmp_dir = Path(tmp)
        concat_mp4 = await _render_absolute_concat_timeline(
            layers,
            width=target_w,
            height=target_h,
            mux_t=mux_t,
            tmp_dir=tmp_dir,
        )

        with_audio = tmp_dir / "with_audio.mp4"
        mux_cmd: list[str] = ["ffmpeg", "-y", "-i", str(concat_mp4), "-i", str(audio_path)]
        voice_gain = max(float(settings.assembly_voice_gain), 0.1)
        voice_filter = f"[1:a]volume={voice_gain:.4f}[voice]"

        if bgm is not None and bgm.path.is_file():
            bgm_gain = max(bgm.level, 0.0) * max(float(settings.assembly_bgm_mix_ratio), 0.0)
            trim = f"atrim=0:{mux_t:.3f},"
            filter_complex = (
                f"{voice_filter};"
                f"[2:a]volume={bgm_gain:.4f},{trim}asetpts=PTS-STARTPTS[bgm];"
                f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=1[aout]"
            )
            mux_cmd.extend([
                "-stream_loop", "-1",
                "-i", str(bgm.path),
                "-filter_complex", filter_complex,
                "-map", "0:v:0", "-map", "[aout]",
            ])
            logger.info(
                "assembly: mixing BGM {} (gain {:.2f}, voice {:.2f})",
                bgm.path.name,
                bgm_gain,
                voice_gain,
            )
        else:
            mux_cmd.extend([
                "-filter_complex", f"{voice_filter}",
                "-map", "0:v:0", "-map", "[voice]",
            ])
            if bgm is not None:
                logger.warning("assembly: BGM path missing, voice only: {}", bgm.path)
            else:
                logger.info("assembly: voice only (gain {:.2f})", voice_gain)

        mux_cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"])
        mux_cmd.extend(["-metadata", f"comment={ASSEMBLY_ENGINE}"])
        mux_cmd.extend(["-t", f"{mux_t:.3f}"])
        mux_cmd.append(str(with_audio))
        await _run(mux_cmd)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        if subtitles_ass is not None and subtitles_ass.exists():
            import shutil
            tmp_ass = tmp_dir / SUBTITLES_ASS_NAME
            shutil.copy2(subtitles_ass, tmp_ass)
            burn_cmd = [
                "ffmpeg", "-y",
                "-i", str(with_audio),
                "-vf", subtitles_vf_arg(),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
                "-c:a", "copy",
            ]
            burn_cmd.extend(["-t", f"{mux_t:.3f}"])
            burn_cmd.append(str(out_path))
            await _run(burn_cmd, cwd=tmp_dir)
        else:
            copy_cmd = ["ffmpeg", "-y", "-i", str(with_audio), "-c", "copy"]
            copy_cmd.extend(["-t", f"{mux_t:.3f}"])
            copy_cmd.append(str(out_path))
            await _run(copy_cmd)

    logger.info("assembly done → {}", out_path)
    return out_path


def make_simple_ass(
    frames: list[tuple[float, float, str]],
    path: Path,
    *,
    width: int | None = None,
    height: int | None = None,
) -> Path:
    """ASS-субтитры под фактическое разрешение финального ролика."""
    w = width or DEFAULT_ASS_W
    h = height or DEFAULT_ASS_H
    _, _, ass_prefix = subtitle_layout(w, h)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour,"
        " Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR,"
        " MarginV, Encoding\n"
        "Style: Default,Inter,74,&H00FFFFFF,&H00000000,&H00000000,1,0,1,5,1,2,80,80,220,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    body_lines: list[str] = []
    for s, e, text in frames:
        line = f"{ass_prefix}{_escape_ass(text)}"
        body_lines.append(
            f"Dialogue: 0,{_fmt_ts(s)},{_fmt_ts(e)},Default,,0,0,0,,{line}"
        )
    path.write_text(header + "\n".join(body_lines) + "\n", encoding="utf-8")
    return path


def _fmt_ts(t: float) -> str:
    t = max(t, 0.0)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _escape_ass(text: str) -> str:
    return (text or "").replace("\n", r"\N").replace("{", "[").replace("}", "]")
