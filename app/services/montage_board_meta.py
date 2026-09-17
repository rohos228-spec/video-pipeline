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
    """Ключ слота доски: image → ``N:imageS``, video → ``N:S``, coverage → ``N:plan|action|kind``."""
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
    if t == "coverage_plan":
        return f"{fr}:plan"
    if t == "coverage_action":
        return f"{fr}:action"
    if t == "coverage_template":
        return f"{fr}:template"
    if t == "coverage_anchors":
        return f"{fr}:anchors"
    if t == "coverage_angle":
        return f"{fr}:angle"
    if t == "coverage_move":
        return f"{fr}:move"
    if t == "coverage_stitch":
        return f"{fr}:stitch"
    if t == "coverage_light":
        return f"{fr}:light"
    if t == "coverage_set":
        return f"{fr}:set"
    if t == "coverage_scene_action":
        return f"{fr}:scene_action"
    if t == "coverage_sense":
        return f"{fr}:sense"
    if t == "coverage_visual_type":
        return f"{fr}:visual_type"
    if t == "coverage_place":
        return f"{fr}:place"
    if t == "coverage_characters":
        return f"{fr}:characters"
    if t == "coverage_props":
        return f"{fr}:props"
    if t == "coverage_bg":
        return f"{fr}:bg"
    if t == "coverage_accent":
        return f"{fr}:accent"
    if t == "coverage_feature":
        return f"{fr}:feature"
    if t.startswith("coverage_"):
        return f"{fr}:kind"
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


"""Текстовые поля coverage-операций, которые обязаны дожить до apply.

Строки сцены на доске (крупность / ракурс / движение / стык / свет / набор)
пишут значение в саму операцию: потерянное поле = apply с пустым значением.
"""
COVERAGE_TEXT_FIELDS = (
    "plan",
    "action",
    "kind",
    "prompt",
    "correction",
    "template",
    "angle",
    "move",
    "stitch",
    "light",
    "set",
    "sense",
    "visual_type",
    "place",
    "characters",
    "props",
    "bg",
    "accent",
    "feature",
)


def _keep_anchor_hints(
    rows: list[dict[str, Any]], raw_rows: Any
) -> list[dict[str, Any]]:
    """Чей это якорь: без ``cell_index``/``frame_number`` доска после
    перезагрузки не покажет дописанный якорь в своей клетке. В биты[] эти
    подсказки не попадают — ``normalize_anchor_rows`` их снимает при apply."""
    hints: list[dict[str, Any] | None] = []
    for item in raw_rows or []:
        if isinstance(item, str):
            if item.strip():
                hints.append(None)
            continue
        if not isinstance(item, dict):
            continue
        if str(item.get("якорь") or item.get("anchor") or "").strip():
            hints.append(item)
    for i, row in enumerate(rows):
        hint = hints[i] if i < len(hints) else None
        if hint is None:
            continue
        for key in ("cell_index", "frame_number"):
            val = hint.get(key)
            if isinstance(val, int):
                row[key] = val
    return rows


def normalize_queue_ops(raw_ops: Any) -> list[dict[str, Any]]:
    """Очередь от UI → только известные типы, кадры и поля правок."""
    cleaned: list[dict[str, Any]] = []
    for raw in raw_ops or []:
        if not isinstance(raw, dict):
            continue
        op_type = str(raw.get("type") or "")
        try:
            frame_number = int(raw.get("frame_number"))
        except (TypeError, ValueError):
            continue
        if frame_number < 1:
            continue
        item: dict[str, Any] = {
            "type": op_type,
            "frame_number": frame_number,
            "shot": 2 if raw.get("shot") == 2 else 1,
        }
        if op_type.startswith(("image_", "video_")):
            for key in ("prompt", "correction", "instruction"):
                val = raw.get(key)
                if isinstance(val, str) and val.strip():
                    item[key] = val
            cleaned.append(item)
            continue
        if not op_type.startswith("coverage_"):
            continue
        for key in COVERAGE_TEXT_FIELDS:
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                item[key] = val.strip()
        if isinstance(raw.get("anchors"), list):
            from app.services.montage_scene_editor import normalize_anchor_rows

            rows = normalize_anchor_rows(raw["anchors"])
            if rows:
                item["anchors"] = _keep_anchor_hints(rows, raw["anchors"])
        parent_raw = raw.get("parent_number")
        if parent_raw not in (None, ""):
            try:
                item["parent_number"] = int(parent_raw)
            except (TypeError, ValueError):
                pass
        cleaned.append(item)
    return cleaned


def _remap_slot_key(key: str, mapping: dict[int, int]) -> str:
    head, sep, tail = str(key).partition(":")
    try:
        number = int(head)
    except (TypeError, ValueError):
        return str(key)
    return f"{mapping.get(number, number)}{sep}{tail}"


def drop_pending_ops_for_frames(
    board: dict[str, Any], frame_numbers: set[int]
) -> int:
    """Удаление кадра не должно затирать очередь соседей — только его ops."""
    if not frame_numbers:
        return 0
    ops = list(board.get("pending_ops") or [])
    kept: list[dict[str, Any]] = []
    dropped = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        try:
            fr = int(op.get("frame_number") or 0)
        except (TypeError, ValueError):
            fr = 0
        if fr in frame_numbers:
            dropped += 1
            continue
        try:
            parent = int(op.get("parent_number") or 0)
        except (TypeError, ValueError):
            parent = 0
        if parent in frame_numbers:
            op = dict(op)
            op.pop("parent_number", None)
        kept.append(op)
    board["pending_ops"] = kept
    return dropped


def remap_frame_numbers(ops: list[dict[str, Any]], mapping: dict[int, int]) -> int:
    """Вставка шота сдвинула нумерацию — правим кадры в ещё не применённых ops."""
    if not mapping:
        return 0
    changed = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        for key in ("frame_number", "parent_number"):
            try:
                number = int(op.get(key))
            except (TypeError, ValueError):
                continue
            new = mapping.get(number)
            if new is not None and new != number:
                op[key] = new
                changed += 1
    return changed


def remap_board_slot_keys(board: dict[str, Any], mapping: dict[int, int]) -> None:
    """Подсветка и trim'ы после renumber должны остаться на своих кадрах."""
    if not mapping:
        return
    for key in ("highlights", "failed_highlights", "stale_videos"):
        raw = board.get(key)
        if isinstance(raw, list):
            board[key] = [_remap_slot_key(str(x), mapping) for x in raw]
    for key in ("video_trims", "corrections"):
        raw = board.get(key)
        if isinstance(raw, dict):
            board[key] = {
                _remap_slot_key(str(k), mapping): v for k, v in raw.items()
            }


def should_accept_queue_save(
    *,
    cleaned: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    apply_running: bool,
    force_clear: bool = False,
) -> tuple[bool, str | None]:
    """Защита от случайного затирания pending_ops через POST /queue.

    Apply сам ужимает очередь через set_montage_meta — этот guard только для
    клиентских/диагностических POST.
    """
    if apply_running and len(cleaned) < len(existing):
        return False, "apply_running"
    if len(cleaned) == 0 and len(existing) > 0 and not force_clear:
        return False, "refuse_empty_overwrite"
    return True, None


def touch_applied(board: dict[str, Any]) -> None:
    board["applied_at"] = datetime.now(timezone.utc).isoformat()
