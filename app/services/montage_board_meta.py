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


def drop_highlight(board: dict[str, Any], key: str) -> None:
    board["highlights"] = [h for h in (board.get("highlights") or []) if h != key]


def drop_pending_ops_for_media(
    board: dict[str, Any],
    frame_number: int,
    *,
    shot: int,
    media: str,
) -> int:
    """Убрать из очереди apply ops для кадра/шота (image|video), чтобы regen не вернул файл после Delete."""
    ops = board.get("pending_ops") or []
    if not isinstance(ops, list):
        board["pending_ops"] = []
        return 0
    prefix = "image" if media == "image" else "video"
    kept: list[Any] = []
    dropped = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        try:
            fn = int(op.get("frame_number") or 0)
            sh = int(op.get("shot") or 1)
        except (TypeError, ValueError):
            kept.append(op)
            continue
        t = str(op.get("type") or "")
        if fn == frame_number and sh == shot and t.startswith(prefix):
            dropped += 1
            continue
        kept.append(op)
    board["pending_ops"] = kept
    return dropped


def media_tombstone_key(frame_number: int, shot: int, *, media: str) -> str:
    if media == "video":
        return trim_key(frame_number, shot)
    return f"{frame_number}:image{shot}"


def mark_media_deleted(board: dict[str, Any], frame_number: int, shot: int, *, media: str) -> None:
    """Пометить кадр удалённым — in-flight apply не должен вернуть файл через finalize."""
    key = media_tombstone_key(frame_number, shot, media=media)
    deleted = list(board.get("deleted_media") or [])
    if key not in deleted:
        deleted.append(key)
    board["deleted_media"] = deleted


def clear_media_deleted(board: dict[str, Any], frame_number: int, shot: int, *, media: str) -> None:
    key = media_tombstone_key(frame_number, shot, media=media)
    board["deleted_media"] = [k for k in (board.get("deleted_media") or []) if k != key]


def is_media_deleted(board: dict[str, Any] | None, frame_number: int, shot: int, *, media: str) -> bool:
    if not board:
        return False
    key = media_tombstone_key(frame_number, shot, media=media)
    return key in (board.get("deleted_media") or [])


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
