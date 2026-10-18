"""Ячейка обнулила или укоротила ``кадры[]``, и id не уехал в ту же сцену.

Детектор только читает. Функции монтажа не чинит.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.entities import (
    cell_scene_key,
    collect_cells,
    frame_id,
    frame_is_exempt,
    text_of,
)
from app.orchestrator.validators.violations import Violation, sorted_violations

_IDLESS_TEXT_KEYS = ("закадр", "voiceover", "vo", "действие", "текст", "text", "описание")


def check_frames_cleared(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Короче или пусто, и живого id нет в другой ячейке той же сцены.

    Несписковое ``кадры`` — не этот код. Неактивный кадр может уйти.
    """

    del ops
    if before is None or after is None:
        return []
    before_cells = [cell for cell in collect_cells(before) if isinstance(cell.get("кадры"), list)]
    after_cells = collect_cells(after)
    after_by_id: dict[str, dict[str, Any]] = {}
    present: dict[str, set[str]] = {}
    for cell in after_cells:
        cid = _cell_id(cell)
        if cid and cid not in after_by_id:
            after_by_id[cid] = cell
        bucket = present.setdefault(cell_scene_key(cell), set())
        shots = cell.get("кадры")
        if not isinstance(shots, list):
            continue
        for shot in shots:
            token = _shot_token(shot)
            if token:
                bucket.add(token)
    violations: list[Violation] = []
    seen: set[str] = set()
    for cell in before_cells:
        shots = cell.get("кадры")
        if not isinstance(shots, list):
            continue
        cid = _cell_id(cell)
        after_cell = after_by_id.get(cid) if cid else None
        if after_cell is None:
            after_shots: list[Any] = []
        else:
            raw = after_cell.get("кадры")
            if not isinstance(raw, list):
                continue
            after_shots = raw
        if len(after_shots) >= len(shots):
            continue
        scene = cell_scene_key(cell)
        kept = present.get(scene, set())
        for shot in shots:
            if frame_is_exempt(shot):
                continue
            token = _shot_token(shot)
            if not token or token in kept or token in seen:
                continue
            seen.add(token)
            path = f"scenes/{scene}/кадры/{token}" if scene else f"кадры/{token}"
            violations.append(
                Violation(code="frames_cleared", severity="error", path=path, id=token)
            )
    return sorted_violations(violations)


def _shot_token(shot: Any) -> str | None:
    """id шота, иначе текст, иначе ``parent:<id>``. Пустой id — не id."""

    fid = frame_id(shot)
    if fid:
        return fid
    if not isinstance(shot, dict):
        return None
    for key in _IDLESS_TEXT_KEYS:
        text = text_of(shot.get(key))
        if text:
            return text
    parent = shot.get("parent_id")
    if parent not in (None, ""):
        return f"parent:{parent}"
    return None


def _cell_id(cell: dict[str, Any]) -> str:
    fid = frame_id(cell)
    if fid:
        return fid
    raw = cell.get("uuid")
    return "" if raw in (None, "") else str(raw)
