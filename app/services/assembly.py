"""FFmpeg-сборка финального ролика.

Вход:
  - список (clip_path: mp4 на 8 сек, duration: float) в порядке кадров,
  - путь к единой mp3-озвучке,
  - путь выходного mp4.

Что делаем:
  1. Для каждого клипа — обрезаем до точной длительности `duration` (c начала)
     и приводим к канве 1080x1920 с центровкой (чтобы все клипы были 9:16).
  2. Склеиваем через ffmpeg concat с перекодированием (надёжнее, чем demuxer).
  3. Накладываем mp3 как основную звуковую дорожку (-map 0:v -map 1:a -shortest).
  4. (Опционально) — прожигаем ASS-субтитры, если передан путь.

Используем просто subprocess с ffmpeg (без python-биндингов), чтобы не плодить
зависимостей сверх нужного.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

CANVAS_W, CANVAS_H = 1080, 1920
FPS = 30


@dataclass
class ClipSpec:
    src: Path
    duration: float  # требуемая длительность после обрезки


async def _run(cmd: list[str]) -> None:
    logger.debug("$ {}", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("ffmpeg stderr:\n{}", stderr.decode(errors="ignore"))
        raise RuntimeError(f"ffmpeg exited with {proc.returncode}")


async def _cut_and_normalize(src: Path, duration: float, dst: Path) -> None:
    """Обрезает src до `duration` секунд и приводит к 1080x1920@30, без звука."""
    vf = (
        f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease,"
        f"pad={CANVAS_W}:{CANVAS_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1"
    )
    await _run([
        "ffmpeg", "-y",
        "-i", str(src),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-r", str(FPS),
        "-an",  # отбросим оригинальный звук
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
        str(dst),
    ])


async def assemble(
    clips: list[ClipSpec],
    audio_path: Path,
    out_path: Path,
    *,
    subtitles_ass: Path | None = None,
) -> Path:
    if not clips:
        raise ValueError("нет клипов для сборки")

    with tempfile.TemporaryDirectory(prefix="vp_asm_") as tmp:
        tmp_dir = Path(tmp)
        normalized: list[Path] = []

        for i, spec in enumerate(clips):
            dst = tmp_dir / f"clip_{i:03d}.mp4"
            await _cut_and_normalize(spec.src, spec.duration, dst)
            normalized.append(dst)

        # concat demuxer
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
            "-r", str(FPS),
            "-an",
            str(concat_mp4),
        ])

        # наложим аудио
        with_audio = tmp_dir / "with_audio.mp4"
        await _run([
            "ffmpeg", "-y",
            "-i", str(concat_mp4),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(with_audio),
        ])

        # субтитры (если есть)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if subtitles_ass is not None and subtitles_ass.exists():
            import shutil
            tmp_ass = tmp_dir / "subs.ass"
            shutil.copy2(subtitles_ass, tmp_ass)
            # Use subtitles filter with filename= key to avoid ffmpeg
            # parsing the path as positional original_size argument.
            # Forward slashes + escaped colons for Windows drive letters.
            esc = tmp_ass.resolve().as_posix().replace(":", "\\\\:")
            await _run([
                "ffmpeg", "-y",
                "-i", str(with_audio),
                "-vf", f"subtitles=filename='{esc}'",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
                "-c:a", "copy",
                str(out_path),
            ])
        else:
            # просто переносим
            await _run(["ffmpeg", "-y", "-i", str(with_audio), "-c", "copy", str(out_path)])

    logger.info("assembly done → {}", out_path)
    return out_path


def make_simple_ass(
    frames: list[tuple[float, float, str]],  # (start_ts, end_ts, text)
    path: Path,
) -> Path:
    """Простейшие ASS-субтитры: крупный белый текст с чёрной обводкой,
    позиция по центру снизу. Для шортсов 9:16."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {CANVAS_W}\n"
        f"PlayResY: {CANVAS_H}\n"
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
        body_lines.append(f"Dialogue: 0,{_fmt_ts(s)},{_fmt_ts(e)},Default,,0,0,0,,{_escape_ass(text)}")
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
