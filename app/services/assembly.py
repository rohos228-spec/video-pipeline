"""FFmpeg-сборка финального ролика.

Вход:
  - клипы mp4 (≈8 с) с подгонкой под Whisper,
  - озвучка mp3,
  - опционально фоновая музыка.

Микш:
  - исходный звук клипов: ASSEMBLY_CLIP_AUDIO_DB (по умолчанию −22 dB),
  - BGM: ASSEMBLY_BGM_DB (по умолчанию −17 dB),
  - голос ElevenLabs — основная дорожка.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.services.clip_fit import plan_clip_fit
from app.services.media_probe import probe_duration
from app.settings import settings

CANVAS_W, CANVAS_H = 1080, 1920
FPS = 30

_CLIP_NUM_RE = re.compile(r"(?:^|/|\\)clip_(\d+)", re.IGNORECASE)


@dataclass
class ClipSpec:
    src: Path
    duration: float  # целевая длительность слота (Whisper)
    frame_number: int | None = None


def parse_frame_number_from_path(path: Path) -> int | None:
    """Первые цифры в имени: clip_003_xxx.mp4 → 3."""
    m = _CLIP_NUM_RE.search(path.as_posix())
    if not m:
        stem = path.stem
        m2 = re.match(r"^(\d+)", stem)
        if m2:
            return int(m2.group(1))
        return None
    return int(m.group(1))


async def _run(cmd: list[str]) -> None:
    logger.debug("$ {}", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("ffmpeg stderr:\n{}", stderr.decode(errors="ignore"))
        raise RuntimeError(f"ffmpeg exited with {proc.returncode}")


def _scale_vf() -> str:
    return (
        f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease,"
        f"pad={CANVAS_W}:{CANVAS_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1"
    )


async def _process_clip(
    src: Path,
    target_duration: float,
    dst: Path,
    *,
    clip_audio_db: float,
    max_stretch_ratio: float,
) -> float:
    """Нормализует клип; возвращает фактическую длительность на таймлайне."""
    source_d = await probe_duration(src)
    plan = plan_clip_fit(source_d, target_duration, max_stretch_ratio=max_stretch_ratio)
    out_d = plan.output_duration
    vf = _scale_vf()
    clip_vol = f"{clip_audio_db}dB"

    if plan.mode == "stretch" and source_d > 0:
        # Замедление: source → out_d (не более +15% к длине исходника).
        factor = source_d / out_d
        await _run([
            "ffmpeg", "-y",
            "-i", str(src),
            "-filter_complex",
            (
                f"[0:v]{vf},setpts=PTS*{factor:.6f}[v];"
                f"[0:a]volume={clip_vol},atempo={out_d / source_d:.6f}[a]"
            ),
            "-map", "[v]", "-map", "[a]",
            "-r", str(FPS),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            str(dst),
        ])
    elif plan.mode == "use_source":
        await _run([
            "ffmpeg", "-y",
            "-i", str(src),
            "-t", f"{out_d:.3f}",
            "-vf", vf,
            "-af", f"volume={clip_vol}",
            "-r", str(FPS),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            str(dst),
        ])
    else:
        await _run([
            "ffmpeg", "-y",
            "-i", str(src),
            "-t", f"{out_d:.3f}",
            "-vf", vf,
            "-af", f"volume={clip_vol}",
            "-r", str(FPS),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            str(dst),
        ])
    return out_d


async def assemble(
    clips: list[ClipSpec],
    audio_path: Path,
    out_path: Path,
    *,
    subtitles_ass: Path | None = None,
    bgm_path: Path | None = None,
    clip_audio_db: float | None = None,
    bgm_db: float | None = None,
    max_stretch_ratio: float | None = None,
) -> Path:
    if not clips:
        raise ValueError("нет клипов для сборки")

    clip_db = settings.assembly_clip_audio_db if clip_audio_db is None else clip_audio_db
    bgm_level_db = settings.assembly_bgm_db if bgm_db is None else bgm_db
    stretch = (
        settings.assembly_max_stretch_ratio
        if max_stretch_ratio is None
        else max_stretch_ratio
    )

    t_cursor = 0.0
    with tempfile.TemporaryDirectory(prefix="vp_asm_") as tmp:
        tmp_dir = Path(tmp)
        normalized: list[Path] = []

        for i, spec in enumerate(clips):
            dst = tmp_dir / f"clip_{i:03d}.mp4"
            actual = await _process_clip(
                spec.src,
                spec.duration,
                dst,
                clip_audio_db=clip_db,
                max_stretch_ratio=stretch,
            )
            normalized.append(dst)
            t_cursor += actual

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
            "-c:a", "aac", "-b:a", "128k",
            "-r", str(FPS),
            str(concat_mp4),
        ])

        # Голос + опционально BGM поверх звука клипов
        mixed_audio = tmp_dir / "mixed_audio.m4a"
        if bgm_path is not None and bgm_path.exists():
            await _run([
                "ffmpeg", "-y",
                "-i", str(concat_mp4),
                "-i", str(audio_path),
                "-i", str(bgm_path),
                "-filter_complex",
                (
                    "[0:a]volume=1[amb];"
                    f"[1:a]volume=1[voice];"
                    f"[2:a]volume={bgm_level_db}dB,aloop=loop=-1:size=2e+09[bgm];"
                    "[amb][voice][bgm]amix=inputs=3:duration=first:dropout_transition=0[a]"
                ),
                "-map", "0:v:0", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(mixed_audio),
            ])
        else:
            await _run([
                "ffmpeg", "-y",
                "-i", str(concat_mp4),
                "-i", str(audio_path),
                "-filter_complex",
                "[0:a]volume=1[amb];[1:a]volume=1[voice];[amb][voice]amix=inputs=2:duration=first[a]",
                "-map", "0:v:0", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(mixed_audio),
            ])

        out_path.parent.mkdir(parents=True, exist_ok=True)
        if subtitles_ass is not None and subtitles_ass.exists():
            esc = subtitles_ass.resolve().as_posix().replace("'", r"\'")
            await _run([
                "ffmpeg", "-y",
                "-i", str(mixed_audio),
                "-vf", f"ass='{esc}'",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
                "-c:a", "copy",
                str(out_path),
            ])
        else:
            await _run(["ffmpeg", "-y", "-i", str(mixed_audio), "-c", "copy", str(out_path)])

    logger.info("assembly done → {} ({} clips, {:.1f}s)", out_path, len(clips), t_cursor)
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
        body_lines.append(
            f"Dialogue: 0,{_fmt_ts(s)},{_fmt_ts(e)},Default,,0,0,0,,{_escape_ass(text)}"
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
