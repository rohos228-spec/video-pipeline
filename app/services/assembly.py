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
from app.services.media_probe import probe_video_size

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
    src: Path
    duration: float  # требуемая длительность после обрезки


async def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    logger.debug("$ {}", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd is not None else None,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("ffmpeg stderr:\n{}", stderr.decode(errors="ignore"))
        raise RuntimeError(f"ffmpeg exited with {proc.returncode}")


async def _cut_clip(
    src: Path,
    duration: float,
    dst: Path,
    *,
    target_w: int | None = None,
    target_h: int | None = None,
) -> None:
    """Обрезка по времени; при несовпадении размера — scale+pad под эталон."""
    cmd: list[str] = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-t", f"{duration:.3f}",
    ]
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
    await _run(cmd)


async def _resolve_assembly_target_size(clips: list[ClipSpec]) -> tuple[int, int]:
    """Эталон = размер первого клипа в таймлайне; остальные подгоняем."""
    target_w, target_h = await probe_video_size(clips[0].src)
    for spec in clips[1:]:
        w2, h2 = await probe_video_size(spec.src)
        if (w2, h2) != (target_w, target_h):
            logger.warning(
                "assembly: {} {}x{} → нормализация под {}x{} (Outsee отдал другой aspect)",
                spec.src.name,
                w2,
                h2,
                target_w,
                target_h,
            )
    logger.info("assembly: целевое видео {}x{}", target_w, target_h)
    return target_w, target_h


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

    target_w, target_h = await _resolve_assembly_target_size(clips)

    with tempfile.TemporaryDirectory(prefix="vp_asm_") as tmp:
        tmp_dir = Path(tmp)
        normalized: list[Path] = []

        for i, spec in enumerate(clips):
            dst = tmp_dir / f"clip_{i:03d}.mp4"
            src_w, src_h = await probe_video_size(spec.src)
            scale_to = (
                (target_w, target_h)
                if (src_w, src_h) != (target_w, target_h)
                else (None, None)
            )
            await _cut_clip(
                spec.src,
                spec.duration,
                dst,
                target_w=scale_to[0],
                target_h=scale_to[1],
            )
            normalized.append(dst)

        list_file = tmp_dir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in normalized),
            encoding="utf-8",
        )
        concat_mp4 = tmp_dir / "concat.mp4"
        await _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
            "-an",
            str(concat_mp4),
        ])

        with_audio = tmp_dir / "with_audio.mp4"
        mux_cmd: list[str] = ["ffmpeg", "-y", "-i", str(concat_mp4), "-i", str(audio_path)]

        if bgm is not None and bgm.path.is_file():
            bgm_gain = max(bgm.level, 0.0) * 0.50
            trim = f"atrim=0:{max_duration:.3f}," if max_duration is not None else ""
            filter_complex = (
                f"[2:a]volume={bgm_gain:.4f},{trim}asetpts=PTS-STARTPTS[bgm];"
                f"[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
            )
            mux_cmd.extend([
                "-stream_loop", "-1",
                "-i", str(bgm.path),
                "-filter_complex", filter_complex,
                "-map", "0:v:0", "-map", "[aout]",
            ])
            logger.info("assembly: mixing BGM {} (gain {:.2f})", bgm.path.name, bgm_gain)
        else:
            mux_cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
            if bgm is not None:
                logger.warning("assembly: BGM path missing, voice only: {}", bgm.path)

        mux_cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest"])
        if max_duration is not None:
            mux_cmd.extend(["-t", f"{max_duration:.3f}"])
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
                "-shortest",
            ]
            if max_duration is not None:
                burn_cmd.extend(["-t", f"{max_duration:.3f}"])
            burn_cmd.append(str(out_path))
            await _run(burn_cmd, cwd=tmp_dir)
        else:
            copy_cmd = ["ffmpeg", "-y", "-i", str(with_audio), "-c", "copy", "-shortest"]
            if max_duration is not None:
                copy_cmd.extend(["-t", f"{max_duration:.3f}"])
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
