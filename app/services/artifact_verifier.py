from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
from pathlib import Path

MAX_BYTES = 256 * 1024 * 1024
_LINKER_WARNING = b"no version information available"


def probe_stderr_blocks(stderr: bytes) -> bool:
    """Предупреждение динамического линкера не делает файл бракованным.

    tmux кладёт свой libncurses в LD_LIBRARY_PATH, и ffprobe пишет в stderr
    «no version information available», хотя код возврата 0 и JSON годный.
    """
    for raw in stderr.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith((b"ffprobe:", b"ffmpeg:")) and _LINKER_WARNING in line:
            continue
        return True
    return False


def contained_path(path: Path, root: Path) -> Path:
    base, target = Path(root).resolve(), Path(path).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError("artifact path is outside project directory")
    return target


def _sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def _subtitle_info(path, expected):
    text = path.read_text(encoding="utf-8-sig")
    if "[Events]" not in text or "ScriptType: v4.00+" not in text:
        raise ValueError("invalid ASS structure")
    def timestamp(raw):
        if not re.fullmatch(r"\d+:\d{2}:\d{2}\.\d{2}", raw):
            raise ValueError("invalid ASS timestamp")
        h, m, s = raw.split(":")
        if int(m) >= 60 or float(s) >= 60:
            raise ValueError("invalid timestamp range")
        return int(h) * 3600 + int(m) * 60 + float(s)
    intervals = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10 or not re.sub(r"\{[^}]*\}", "", fields[9]).strip():
            raise ValueError("empty or invalid subtitle cue")
        start, end = timestamp(fields[1]), timestamp(fields[2])
        if end <= start or start < 0 or (intervals and start < intervals[-1][1]):
            raise ValueError("invalid or overlapping subtitle interval")
        if end > expected.get("max_duration", float("inf")):
            raise ValueError("subtitle exceeds media duration")
        intervals.append((start, end))
    if not intervals:
        raise ValueError("no subtitle cues")
    return {"format": "ass", "cue_count": len(intervals), "duration": intervals[-1][1]}


async def verify_artifact(path: Path, *, root: Path, kind: str, expected=None) -> dict:
    target = contained_path(path, root)
    result = {"status": "unknown", "kind": kind, "semantic_quality_verified": False}
    expected = expected or {}
    if not target.is_file() or not 0 < target.stat().st_size <= MAX_BYTES:
        return {**result, "status": "failed", "reason": "missing_empty_or_oversized_file"}
    result.update(bytes=target.stat().st_size, sha256=_sha256(target))
    try:
        if kind == "image":
            from PIL import Image
            with Image.open(target) as picture:
                if picture.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise ValueError("unsupported image format")
                info = {"format": picture.format, "width": picture.width, "height": picture.height}
                if picture.width * picture.height > 40_000_000:
                    raise ValueError("image pixel limit exceeded")
                picture.verify()
            with Image.open(target) as picture:
                picture.load()
        elif kind == "subtitles":
            info = _subtitle_info(target, expected)
        elif kind in {"video", "audio", "assembly"}:
            if not shutil.which("ffprobe"):
                return {**result, "reason": "ffprobe_unavailable"}
            process = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-show_streams", "-show_format", "-of", "json", str(target),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.communicate()
                raise
            if process.returncode or len(stdout) > 1024 * 1024 or probe_stderr_blocks(stderr):
                raise ValueError("media probe rejected file")
            data = json.loads(stdout)
            streams = data.get("streams", [])
            types = {s.get("codec_type") for s in streams}
            required = {"audio"} if kind == "audio" else {"video", "audio"} if kind == "assembly" else {"video"}
            if not required <= types:
                raise ValueError("required media stream missing")
            duration = float(data.get("format", {}).get("duration", 0))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("invalid duration")
            info = {"format": data.get("format", {}).get("format_name"), "duration": duration,
                    "streams": sorted(t for t in types if t)}
            if not info["format"]:
                raise ValueError("container format unknown")
            if "video" in types:
                video = next(s for s in streams if s.get("codec_type") == "video")
                info.update(width=video.get("width", 0), height=video.get("height", 0))
                if info["width"] <= 0 or info["height"] <= 0:
                    raise ValueError("invalid video dimensions")
        else:
            return {**result, "reason": "unsupported_result_kind"}
        for key in ("width", "height"):
            if key in expected and expected[key] != info.get(key):
                raise ValueError("dimensions differ from contract")
        if "duration" in info and not expected.get("min_duration", 0) <= info["duration"] <= expected.get("max_duration", float("inf")):
            raise ValueError("duration differs from contract")
        return {**result, **info, "status": "passed"}
    except (ValueError, OSError, SyntaxError, asyncio.TimeoutError) as exc:
        return {**result, "status": "failed", "reason": type(exc).__name__}
