"""Шот был в сцене и пропал, не будучи ``активен: False``.

Ловит, в частности, выкидывание чужого места в
``align_shots_to_action_chain``. Саму функцию не чинит и ничего не пишет.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.entities import frame_id, iter_shots
from app.orchestrator.validators.grammar import shot_counts_as_active
from app.orchestrator.validators.violations import Violation, sorted_violations


def check_shot_dropped(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    del ops
    if before is None or after is None:
        return []
    after_ids = {fid for shot in iter_shots(after) if (fid := frame_id(shot))}
    violations: list[Violation] = []
    seen: set[str] = set()
    for shot in iter_shots(before):
        fid = frame_id(shot)
        if not fid or fid in seen:
            continue
        seen.add(fid)
        if not shot_counts_as_active(shot) or fid in after_ids:
            continue
        scene = shot.get("сцена") if isinstance(shot, dict) else None
        scene_key = "" if scene in (None, "") else str(scene)
        path = f"scenes/{scene_key}/кадры/{fid}" if scene_key else f"кадры/{fid}"
        violations.append(
            Violation(code="shot_dropped", severity="error", path=path, id=fid)
        )
    return sorted_violations(violations)
