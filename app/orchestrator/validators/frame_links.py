"""FrameText и FrameEdge не пропадают физически. Только чтение."""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.entities import text_of
from app.orchestrator.validators.violations import Violation, sorted_violations

_TEXT_KEYS = ("frame_texts", "texts")
_EDGE_KEYS = ("frame_edges", "edges")


def check_frame_links(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Текст и ребро на месте. ``voiceover`` → ``vo_archived`` — не удаление."""

    del ops
    if before is None or after is None:
        return []
    return sorted_violations(
        _missing_texts(before, after) + _missing_edges(before, after)
    )


def _missing_texts(before: Any, after: Any) -> list[Violation]:
    before_rows = _rows(before, _TEXT_KEYS)
    after_rows = _rows(after, _TEXT_KEYS)
    if not before_rows:
        return []
    after_ids = _ids(after_rows)
    violations: list[Violation] = []
    for row in before_rows:
        rid = _row_id(row)
        if rid and rid in after_ids:
            continue
        if _voiceover_archived(row, after_rows):
            continue
        if not rid and _same_text(row, after_rows):
            continue
        token = rid or _text_token(row)
        violations.append(
            Violation(
                code="frame_text_deleted",
                severity="error",
                path=f"frame_texts/{token}",
                id=rid,
            )
        )
    return violations


def _missing_edges(before: Any, after: Any) -> list[Violation]:
    before_rows = _rows(before, _EDGE_KEYS)
    after_rows = _rows(after, _EDGE_KEYS)
    if not before_rows:
        return []
    after_ids = _ids(after_rows)
    after_keys = {_edge_key(row) for row in after_rows}
    violations: list[Violation] = []
    for row in before_rows:
        rid = _row_id(row)
        if rid and rid in after_ids:
            continue
        if not rid and _edge_key(row) in after_keys:
            continue
        token = rid or _edge_token(row)
        violations.append(
            Violation(
                code="frame_edge_deleted",
                severity="error",
                path=f"frame_edges/{token}",
                id=rid,
            )
        )
    return violations


def _rows(side: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(side, dict):
        return []
    for key in keys:
        value = side.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _ids(rows: list[dict[str, Any]]) -> set[str]:
    return {rid for row in rows if (rid := _row_id(row))}


def _row_id(row: dict[str, Any]) -> str:
    raw = row.get("id")
    if raw in (None, ""):
        return ""
    return str(raw)


def _voiceover_archived(row: dict[str, Any], after_rows: list[dict[str, Any]]) -> bool:
    if str(row.get("kind") or "") != "voiceover":
        return False
    text = text_of(row.get("text"))
    frame = str(row.get("frame_id") or "")
    for item in after_rows:
        if str(item.get("kind") or "") != "vo_archived":
            continue
        if text_of(item.get("text")) == text and str(item.get("frame_id") or "") == frame:
            return True
    return False


def _same_text(row: dict[str, Any], after_rows: list[dict[str, Any]]) -> bool:
    token = _text_token(row)
    archived = _text_token({**row, "kind": "vo_archived"}) if str(row.get("kind") or "") == "voiceover" else ""
    return any(_text_token(item) == token or (archived and _text_token(item) == archived) for item in after_rows)


def _text_token(row: dict[str, Any]) -> str:
    return "|".join(
        (
            str(row.get("frame_id") or ""),
            str(row.get("kind") or ""),
            text_of(row.get("text")),
        )
    )


def _edge_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("from_frame_id") if row.get("from_frame_id") is not None else row.get("from") or ""),
        str(row.get("to_frame_id") if row.get("to_frame_id") is not None else row.get("to") or ""),
        str(row.get("type") or row.get("kind") or "next"),
    )


def _edge_token(row: dict[str, Any]) -> str:
    left, right, kind = _edge_key(row)
    return f"{left}>{right}:{kind}"
