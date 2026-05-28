"""Длительность медиафайлов через ffprobe."""

from __future__ import annotations

import asyncio
from pathlib import Path


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
