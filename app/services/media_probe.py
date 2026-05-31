"""Длительность медиафайлов через ffprobe."""

from __future__ import annotations

import asyncio
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
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {stderr.decode(errors='ignore')}")
    return max(float(stdout.decode().strip()), 0.01)
