"""Длительность медиафайлов через ffprobe."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def probe_duration(path: Path) -> float:
    """Длительность в секундах. 0.0 если не удалось прочитать."""
    if not path.exists():
        return 0.0
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return 0.0
    try:
        return max(float(stdout.decode().strip()), 0.0)
    except ValueError:
        return 0.0
