"""Проверка дубликатов scene_video: тот же файл или те же первые кадры."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

_FRAME_TIMES_SEC = (0.0, 0.5, 1.0)
_THUMB_SIZE = 64

_fp_cache: dict[str, str] = {}


def _cache_key(path: Path) -> str:
    try:
        st = path.stat()
        return f"{path.resolve()}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return str(path.resolve())


async def _extract_frame_rgb(path: Path, *, at_sec: float) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-ss",
        f"{at_sec:.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={_THUMB_SIZE}:{_THUMB_SIZE}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not stdout:
        err = stderr.decode(errors="ignore")[:200]
        raise RuntimeError(
            f"ffmpeg frame @ {at_sec}s failed for {path.name}: {err}"
        )
    return stdout


async def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def video_content_fingerprint(path: Path) -> str:
    """Отпечаток по размеру + кадрам на 0 / 0.5 / 1 сек."""
    key = _cache_key(path)
    cached = _fp_cache.get(key)
    if cached is not None:
        return cached

    parts: list[bytes] = [str(path.stat().st_size).encode()]
    for t in _FRAME_TIMES_SEC:
        try:
            parts.append(await _extract_frame_rgb(path, at_sec=t))
        except Exception:
            continue
    if len(parts) == 1:
        fp = await file_sha256(path)
    else:
        fp = hashlib.sha256(b"|".join(parts)).hexdigest()
    _fp_cache[key] = fp
    return fp


async def videos_are_duplicates(candidate: Path, reference: Path) -> bool:
    if not candidate.is_file() or not reference.is_file():
        return False
    if candidate.resolve() == reference.resolve():
        return False
    cs, rs = candidate.stat().st_size, reference.stat().st_size
    if cs == rs and cs > 0:
        if await file_sha256(candidate) == await file_sha256(reference):
            return True
    return (
        await video_content_fingerprint(candidate)
        == await video_content_fingerprint(reference)
    )


async def find_duplicate_reference(
    candidate: Path,
    references: list[Path],
) -> Path | None:
    for ref in references:
        if not ref.is_file():
            continue
        if await videos_are_duplicates(candidate, ref):
            return ref
    return None
