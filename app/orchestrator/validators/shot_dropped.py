"""Шот был в сцене и пропал, не будучи ``активен: False``.

Ловит, в частности, выкидывание чужого места в
``align_shots_to_action_chain``. Саму функцию не чинит и ничего не пишет.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.activity_flags import shot_is_active
from app.orchestrator.validators.entities import collect_cells, frame_id, iter_shots, text_of
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


_ANON_KEYS = ("закадр", "voiceover", "vo", "text", "текст", "действие")


def check_marked_shot_removed(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Помеченный шот исчез из «после» целиком.

    ``shot_dropped`` / ``frame_deleted`` / ``frames_cleared`` пропускают
    уже неактивный шот. Здесь активность только ``shot_is_active``.
    Перенос и шот на месте не ошибка: id или текст ещё есть в «после».
    """

    del ops
    if before is None or after is None:
        return []
    after_shots = _listed_shots(after)
    after_ids = {fid for shot in after_shots if (fid := frame_id(shot))}
    after_anon = {_anon_text(shot) for shot in after_shots if frame_id(shot) is None}
    violations: list[Violation] = []
    seen: set[str] = set()
    for shot in _listed_shots(before):
        if shot_is_active(shot):
            continue
        fid = frame_id(shot)
        if fid:
            if fid in after_ids or fid in seen:
                continue
            seen.add(fid)
            token = fid
        else:
            token = _anon_text(shot)
            if not token or token in after_anon or token in seen:
                continue
            seen.add(token)
        violations.append(
            Violation(
                code="marked_shot_removed",
                severity="error",
                path=f"кадры/{token}",
                id=token,
            )
        )
    return sorted_violations(violations)


def _listed_shots(side: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for cell in collect_cells(side):
        shots = cell.get("кадры")
        if not isinstance(shots, list):
            continue
        for shot in shots:
            if isinstance(shot, dict):
                found.append(shot)
    return found


def _anon_text(shot: dict[str, Any]) -> str:
    for key in _ANON_KEYS:
        text = text_of(shot.get(key))
        if text:
            return text
    return ""
