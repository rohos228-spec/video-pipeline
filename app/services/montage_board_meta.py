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
        "corrections": board.get("corrections") or {},
        "pending_ops": list(board.get("pending_ops") or []),
        "applied_at": board.get("applied_at"),
    }


def touch_applied(board: dict[str, Any]) -> None:
    board["applied_at"] = datetime.now(timezone.utc).isoformat()
