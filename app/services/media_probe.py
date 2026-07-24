"""Длительность медиафайлов через ffprobe."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path


async def probe_video_size(path: Path) -> tuple[int, int]:
    """Ширина и высота первого видеопотока (исходное разрешение файла)."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe size failed for {path}: {stderr.decode(errors='ignore')}")
    raw = stdout.decode().strip().split("x")
    if len(raw) != 2:
        raise RuntimeError(f"ffprobe size parse failed for {path}: {stdout!r}")
    w, h = int(raw[0]), int(raw[1])
    if w <= 0 or h <= 0:
        raise RuntimeError(f"invalid video size {w}x{h} for {path}")
    return w, h


async def probe_duration(path: Path) -> float:
    """Реальная длительность: сначала видеопоток, иначе format."""
    # stream duration / nb_frames важнее format — иначе монтаж думает, что
    # клип короче/длиннее фактических кадров.
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration,nb_frames,avg_frame_rate,r_frame_rate",
        "-of", "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _stderr = await proc.communicate()
    if proc.returncode == 0:
        try:
            data = json.loads(stdout.decode() or "{}")
            streams = data.get("streams") or []
            if streams:
                st = streams[0]
                dur_s = st.get("duration")
                if dur_s not in (None, "N/A", ""):
                    return max(float(dur_s), 0.01)
                nb = st.get("nb_frames")
                rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/0"
                if nb not in (None, "N/A", "0", "") and "/" in str(rate):
                    num, den = str(rate).split("/", 1)
                    fps = float(num) / float(den) if float(den) else 0.0
                    if fps > 0:
                        return max(int(nb) / fps, 0.01)
        except (TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError):
            pass

    proc2 = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout2, stderr2 = await proc2.communicate()
    if proc2.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {stderr2.decode(errors='ignore')}")
    return max(float(stdout2.decode().strip()), 0.01)
