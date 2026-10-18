"""Шот был в сцене и пропал, не будучи ``активен: False``.

Ловит, в частности, выкидывание чужого места в
``align_shots_to_action_chain``. Саму функцию не чинит и ничего не пишет.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.entities import (
    collect_scenes,
    frame_id,
    iter_shots,
    scene_id,
    scene_kadry,
    scene_number,
)
from app.orchestrator.validators.grammar import shot_counts_as_active
from app.orchestrator.validators.violations import Violation, sorted_violations


def check_shot_dropped(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    del ops
    if before is None or after is None:
        return []
    after_ids = {fid for shot in iter_shots(after) if (fid := frame_id(shot))}
    hidden = _ids_hidden_by_nodata(before, after)
    violations: list[Violation] = []
    seen: set[str] = set()
    for shot in iter_shots(before):
        fid = frame_id(shot)
        if not fid or fid in seen:
            continue
        seen.add(fid)
        if not shot_counts_as_active(shot) or fid in after_ids or fid in hidden:
            continue
        scene = shot.get("сцена") if isinstance(shot, dict) else None
        scene_key = "" if scene in (None, "") else str(scene)
        path = f"scenes/{scene_key}/кадры/{fid}" if scene_key else f"кадры/{fid}"
        violations.append(
            Violation(code="shot_dropped", severity="error", path=path, id=fid)
        )
    return sorted_violations(violations)


def _ids_hidden_by_nodata(before: Any, after: Any) -> set[str]:
    """Несписковое ``кадры`` — warn, не ``shot_dropped`` на каждый id.

    Id, который ещё числится в списке другой сцены, не прячем.
    """

    after_by_id: dict[str, dict[str, Any]] = {}
    after_by_number: dict[str, dict[str, Any]] = {}
    for scene in collect_scenes(after):
        sid = scene_id(scene)
        number = scene_number(scene)
        if sid and sid not in after_by_id:
            after_by_id[sid] = scene
        if number and number not in after_by_number:
            after_by_number[number] = scene
    nodata: set[str] = set()
    listed: set[str] = set()
    for scene in collect_scenes(before):
        status, frames = scene_kadry(scene)
        if status != "list" or not isinstance(frames, list):
            continue
        sid = scene_id(scene)
        number = scene_number(scene)
        match = after_by_id.get(sid) if sid else None
        if match is None and number:
            match = after_by_number.get(number)
        if match is None:
            continue
        after_status, _after_frames = scene_kadry(match)
        if after_status == "nodata":
            bucket = nodata
        elif after_status == "list":
            bucket = listed
        else:
            continue
        for item in frames:
            fid = frame_id(item)
            if fid:
                bucket.add(fid)
    return nodata - listed
