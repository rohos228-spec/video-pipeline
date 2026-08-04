"""Извлечение 6 кадров из клипа → сетка 3×2 (video_sheet) для vision-check."""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from pathlib import Path

from loguru import logger

from app.services.image_strip import compose_grid_strip

_CLIP_NUM_RE = re.compile(
    r"(?:clip|frame|video_sheet)[_-]?(\d{1,4})",
    re.IGNORECASE,
)
_VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".mkv"})
SHEET_COLS = 3
SHEET_ROWS = 2
FRAMES_PER_CLIP = SHEET_COLS * SHEET_ROWS  # 6


def is_video_path(path: Path) -> bool:
    return path.suffix.lower() in _VIDEO_SUFFIXES


def parse_clip_number(path: Path) -> tuple[int, int] | None:
    """clip_009_….mp4 / video_sheet_009_….png → (9, shot)."""
    name = path.name
    m = _CLIP_NUM_RE.search(name)
    if not m:
        return None
    num = int(m.group(1))
    shot = 2 if re.search(r"(?:_s2_|s2|shot2)", name, re.IGNORECASE) else 1
    return num, shot


def find_shot1_video(videos_dir: Path, frame_number: int) -> Path | None:
    if not videos_dir.is_dir():
        return None
    candidates = [
        p
        for p in videos_dir.glob(f"clip_{frame_number:03d}_*.mp4")
        if "_s2_" not in p.name
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def find_shot2_video(videos_dir: Path, frame_number: int) -> Path | None:
    if not videos_dir.is_dir():
        return None
    candidates = list(videos_dir.glob(f"clip_{frame_number:03d}_s2_*.mp4"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


async def _probe_duration_sec(video: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _stderr = await proc.communicate()
    if proc.returncode != 0:
        return 8.0
    try:
        dur = float((stdout or b"").decode(errors="ignore").strip())
    except ValueError:
        return 8.0
    if dur <= 0.05:
        return 8.0
    return dur


def _sample_times(duration: float, n: int = FRAMES_PER_CLIP) -> list[float]:
    """Центры n равных долей хронометража [0, duration).

    Для 6 кадров при T=8с → 0.667, 2.000, 3.333, 4.667, 6.000, 7.333
    (шаг ровно T/6). Не «от 0 до конца с шагом T/5».
    """
    if n < 1:
        return []
    d = max(0.05, float(duration))
    if n == 1:
        return [min(0.1, d * 0.5)]
    # Чуть не упираемся в последний sample-exact EOF (редко пустой кадр).
    eps = min(0.04, d * 0.002)
    times = [d * (i + 0.5) / n for i in range(n)]
    return [min(t, d - eps) for t in times]


async def _extract_still(video: Path, at_sec: float, out: Path) -> None:
    """Точный seek: ``-ss`` после ``-i`` (иначе keyframe-прыжки ≠ равные доли)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-ss",
        f"{at_sec:.3f}",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 32:
        err = (stderr or b"").decode(errors="ignore")[:240]
        raise RuntimeError(f"ffmpeg still @ {at_sec:.2f}s failed for {video.name}: {err}")


async def build_video_sheet(
    video_path: Path,
    out_dir: Path,
    *,
    frame_number: int | None = None,
    shot: int = 1,
) -> Path:
    """6 stills → PNG/JPEG сетка ``video_sheet_NNN[_s2]_….png``."""
    if not video_path.is_file():
        raise FileNotFoundError(f"build_video_sheet: нет файла {video_path}")
    parsed = parse_clip_number(video_path)
    num = int(frame_number) if frame_number is not None else (parsed[0] if parsed else 0)
    sh = int(shot) if int(shot) in (1, 2) else (parsed[1] if parsed else 1)
    if num < 1:
        raise ValueError(f"build_video_sheet: не удалось взять номер из {video_path.name}")

    dur = await _probe_duration_sec(video_path)
    times = _sample_times(dur, FRAMES_PER_CLIP)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"video_sheet_{num:03d}" + ("_s2" if sh == 2 else "")
    out_base = out_dir / f"{stem}_{video_path.stem[:24]}"

    tmp = Path(tempfile.mkdtemp(prefix="vp_vsheet_"))
    try:
        stills: list[Path] = []
        for i, t in enumerate(times, start=1):
            still = tmp / f"cell_{i:02d}.jpg"
            await _extract_still(video_path, t, still)
            stills.append(still)
        sheet = compose_grid_strip(
            stills,
            out_base,
            cols=SHEET_COLS,
            rows=SHEET_ROWS,
            gutter_px=6,
            max_cell_height=384,
            max_bytes=3_500_000,
        )
        logger.info(
            "video_sheet: {} → {} ({}s, cells={}, t=[{}])",
            video_path.name,
            sheet.name,
            f"{dur:.2f}",
            FRAMES_PER_CLIP,
            ", ".join(f"{t:.2f}" for t in times),
        )
        return sheet
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def materialize_video_sheets_for_check(
    paths: list[Path],
    out_dir: Path,
) -> list[Path]:
    """MP4 во входе → video_sheet PNG; прочие пути (xlsx/txt) оставляем.

    Уже готовые ``video_sheet_*.png|jpg`` пропускаем как есть.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    for p in paths:
        if not p or not p.is_file():
            continue
        name_l = p.name.lower()
        if name_l.startswith("video_sheet_") and p.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        }:
            result.append(p)
            continue
        if not is_video_path(p):
            result.append(p)
            continue
        parsed = parse_clip_number(p)
        if parsed is None:
            logger.warning("video_sheet: skip {}, нет номера кадра в имени", p.name)
            continue
        num, sh = parsed
        try:
            sheet = await build_video_sheet(
                p, out_dir, frame_number=num, shot=sh
            )
            result.append(sheet)
        except Exception:  # noqa: BLE001
            logger.exception("video_sheet: failed for {}", p.name)
            raise
    return result
