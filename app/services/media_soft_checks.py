from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

ASPECT_TOLERANCE = 0.06
CLIP_MIN_SECONDS = 0.5
CLIP_MAX_SECONDS = 120.0
BLACK_SHARE = 0.95
FINAL_MIN_RATIO = 0.7
FINAL_MAX_RATIO = 1.5
FINAL_MAX_SLACK_S = 10.0
FINAL_MIN_COVERAGE = 0.8
SOFT_PREFIX = "soft_"

_ASPECT = re.compile(r"^\s*(\d{1,3})\s*[:_x/]\s*(\d{1,3})\s*$")


def parse_aspect(value: Any) -> float | None:
    m = _ASPECT.match(str(value or ""))
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    return w / h if w > 0 and h > 0 else None


def explicit_aspect(project: Any) -> float | None:
    try:
        from app.services.vibecode_catalog import find_canvas_node, read_node_media_fields
        meta = getattr(project, "meta", None)
        for node_type in ("videos", "images"):
            node = find_canvas_node(meta, node_key=None, node_type=node_type)
            slug = read_node_media_fields(node).get("aspect_slug")
            if parse_aspect(slug):
                return parse_aspect(slug)
    except Exception:
        pass
    return parse_aspect(getattr(project, "aspect_ratio", None))


def aspect_mismatch(width: Any, height: Any, expected: float | None) -> bool:
    try:
        w, h = float(width), float(height)
    except (TypeError, ValueError):
        return False
    if expected is None or w <= 0 or h <= 0:
        return False
    return abs((w / h) - expected) / expected > ASPECT_TOLERANCE


async def black_share(path: Path, duration: float, *, timeout: float = 30.0) -> float | None:
    if not shutil.which("ffmpeg") or not duration or duration <= 0:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-nostats", "-protocol_whitelist", "file,pipe", "-i", str(path), "-an",
            "-vf", "blackdetect=d=0.1:pic_th=0.98:pix_th=0.05", "-f", "null", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return None
        if proc.returncode:
            return None
    except (OSError, ValueError):
        return None
    total = 0.0
    for m in re.finditer(r"black_duration:([0-9.]+)", err.decode(errors="ignore")):
        total += float(m.group(1))
    return min(1.0, total / duration)


async def music_soft_issues(path: Path) -> list[str]:
    from app.services.audio_verification import verify_audio
    try:
        verdict = await asyncio.wait_for(asyncio.to_thread(verify_audio, path), 150)
    except Exception:
        return []
    out = []
    if verdict.silent:
        out.append("music_silent")
    if "bad_format" in verdict.codes:
        out.append("music_bad_format")
    return out


async def clip_soft_issues(path: Path, probe: dict, expected_aspect: float | None) -> list[str]:
    out = []
    if aspect_mismatch(probe.get("width"), probe.get("height"), expected_aspect):
        out.append("clip_aspect_mismatch")
    try:
        duration = float(probe.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration and not CLIP_MIN_SECONDS <= duration <= CLIP_MAX_SECONDS:
        out.append("clip_duration_implausible")
    if duration:
        share = await black_share(path, duration)
        if share is not None and share >= BLACK_SHARE:
            out.append("clip_black")
    return out


def final_soft_issues_from(probe: dict, expected_aspect: float | None, scene_seconds: list[float | None]) -> list[str]:
    out = []
    if aspect_mismatch(probe.get("width"), probe.get("height"), expected_aspect):
        out.append("final_aspect_mismatch")
    known = [float(s) for s in scene_seconds if isinstance(s, (int, float)) and s > 0]
    if scene_seconds and known and len(known) / len(scene_seconds) >= FINAL_MIN_COVERAGE:
        total = sum(known)
        try:
            duration = float(probe.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            if duration < FINAL_MIN_RATIO * total:
                out.append("final_shorter_than_scenes")
            elif duration > FINAL_MAX_RATIO * total + FINAL_MAX_SLACK_S:
                out.append("final_longer_than_scenes")
    return out


async def project_scene_seconds(reader, project_id: int) -> list[float | None]:
    from sqlalchemy import select
    from app.models import Frame
    rows = (await reader.scalars(select(Frame.duration_seconds).where(Frame.project_id == project_id))).all()
    return list(rows)


async def final_soft_issues(reader, project: Any, path: Path, probe: dict | None = None) -> list[str]:
    try:
        if probe is None:
            if not shutil.which("ffprobe"):
                return []
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format",
                "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), 30)
            data = json.loads(out)
            video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            probe = {"width": video.get("width"), "height": video.get("height"),
                     "duration": (data.get("format") or {}).get("duration")}
        return final_soft_issues_from(probe, explicit_aspect(project), await project_scene_seconds(reader, project.id))
    except Exception:
        return []
