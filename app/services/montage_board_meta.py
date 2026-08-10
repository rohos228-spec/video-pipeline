"""meta.montage_board — trim, очередь, подсветка, stale-видео, корректировки."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

MONTAGE_META_KEY = "montage_board"


def montage_meta(project) -> dict[str, Any]:
    raw = getattr(project, "meta", None) or {}
    if not isinstance(raw, dict):
        return {}
    board = raw.get(MONTAGE_META_KEY)
    return deepcopy(board) if isinstance(board, dict) else {}


def set_montage_meta(project, patch: dict[str, Any]) -> dict[str, Any]:
    meta = dict(project.meta or {})
    current = montage_meta(project)
    for key, value in patch.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    if current:
        meta[MONTAGE_META_KEY] = current
    else:
        meta.pop(MONTAGE_META_KEY, None)
    project.meta = meta
    return current


def trim_key(frame_number: int, shot: int) -> str:
    return f"{frame_number}:{shot}"


def mark_stale_videos(board: dict[str, Any], frame_number: int, *, shot: int | None = None) -> None:
    stale = set(board.get("stale_videos") or [])
    if shot is None:
        stale.add(trim_key(frame_number, 1))
        stale.add(trim_key(frame_number, 2))
    else:
        stale.add(trim_key(frame_number, shot))
    board["stale_videos"] = sorted(stale)


def clear_stale_video(board: dict[str, Any], frame_number: int, shot: int) -> None:
    key = trim_key(frame_number, shot)
    stale = [x for x in (board.get("stale_videos") or []) if x != key]
    board["stale_videos"] = stale


def add_highlight(board: dict[str, Any], key: str) -> None:
    highlights = list(board.get("highlights") or [])
    if key not in highlights:
        highlights.append(key)
    board["highlights"] = highlights


def clear_highlights(board: dict[str, Any]) -> None:
    board["highlights"] = []


def slot_key_from_op(op: dict[str, Any] | None) -> str | None:
    """Ключ слота доски: image → ``N:imageS``, video → ``N:S``."""
    if not isinstance(op, dict):
        return None
    t = str(op.get("type") or "")
    try:
        fr = int(op.get("frame_number") or 0)
    except (TypeError, ValueError):
        return None
    if fr < 1:
        return None
    try:
        shot = 2 if int(op.get("shot") or 1) == 2 else 1
    except (TypeError, ValueError):
        shot = 1
    if t.startswith("image_"):
        return f"{fr}:image{shot}"
    if t.startswith("video_"):
        return trim_key(fr, shot)
    return None


def add_failed_highlight(board: dict[str, Any], key: str) -> None:
    failed = list(board.get("failed_highlights") or [])
    if key not in failed:
        failed.append(key)
    board["failed_highlights"] = failed


def clear_failed_highlight(board: dict[str, Any], key: str) -> None:
    board["failed_highlights"] = [
        x for x in (board.get("failed_highlights") or []) if x != key
    ]


def clear_failed_highlights(board: dict[str, Any]) -> None:
    board["failed_highlights"] = []


def failed_highlights_for_public(board: dict[str, Any]) -> list[str]:
    """Явный список + fallback из apply_job.results (после старых прогонов)."""
    stored = board.get("failed_highlights")
    if isinstance(stored, list) and stored:
        return [str(x) for x in stored if str(x).strip()]
    if isinstance(stored, list) and "failed_highlights" in board:
        # Явно пустой после успешного apply — не подмешивать stale results.
        return []
    keys: list[str] = []
    seen: set[str] = set()
    job = board.get("apply_job")
    if not isinstance(job, dict):
        return []
    for raw in job.get("results") or []:
        if not isinstance(raw, dict) or raw.get("ok"):
            continue
        key = slot_key_from_op(raw.get("op") if isinstance(raw.get("op"), dict) else None)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def store_correction(
    board: dict[str, Any],
    frame_number: int,
    shot: int,
    text: str,
) -> None:
    corrections = dict(board.get("corrections") or {})
    corrections[trim_key(frame_number, shot)] = (text or "").strip()
    board["corrections"] = corrections


def get_correction(board: dict[str, Any], frame_number: int, shot: int) -> str:
    corrections = board.get("corrections") or {}
    return str(corrections.get(trim_key(frame_number, shot)) or "").strip()


def public_board_meta(board: dict[str, Any]) -> dict[str, Any]:
    return {
        "video_trims": board.get("video_trims") or {},
        "stale_videos": board.get("stale_videos") or [],
        "highlights": board.get("highlights") or [],
        "failed_highlights": failed_highlights_for_public(board),
        "corrections": board.get("corrections") or {},
        "pending_ops": list(board.get("pending_ops") or []),
        "applied_at": board.get("applied_at"),
    }


def touch_applied(board: dict[str, Any]) -> None:
    board["applied_at"] = datetime.now(timezone.utc).isoformat()
