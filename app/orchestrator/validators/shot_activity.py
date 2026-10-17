"""Неактивный шот не участвует в QC и рендере.

Закадр на неактивном шоте остаётся на месте: в ``закадр_архив`` его не
переносим и сам текст не трогаем. Потребители отфильтровывают такие шоты
через ``shot_is_active`` / ``shots_of``. Это не ``vo_on_inactive``.

Кто живой, решает ``shots_of`` (#371). Этот модуль только читает.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.entities import (
    collect_scenes,
    frame_id,
    scene_id,
    scene_number,
    text_of,
)
from app.orchestrator.validators.grammar import is_kadry_invalid, shot_counts_as_active, shots_of
from app.orchestrator.validators.violations import Violation, sorted_violations

_QC_VERBS = {"qc", "render", "рендер", "qc_shot", "render_shot"}
_ACTIVATE_VERBS = {"activate", "set_active"}
_DEACTIVATE_VERBS = {"deactivate", "set_inactive"}


def check_kadry_invalid(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Несписковое ``кадры`` — warn, а не тихий пустой список."""

    del ops
    violations: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    for side in (before, after):
        for scene in collect_scenes(side):
            if "кадры" not in scene:
                continue
            if not is_kadry_invalid(shots_of({"shots": scene.get("кадры")})):
                continue
            entity = scene_id(scene) or scene_number(scene) or ""
            _add_warn(
                violations,
                seen,
                "kadry_invalid",
                f"scenes/{entity}/кадры" if entity else "кадры",
                entity,
            )
    return sorted_violations(violations)


def check_active_shots(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Проверки живых шотов на снимке ``after`` и на ops."""

    operations = [op for op in list(ops or []) if isinstance(op, dict)]
    violations: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    before_inactive = _inactive_ids(_scene_nodes(before))
    activated = {fid for op in operations if (fid := _targeted(op, True))}
    deactivated = [fid for op in operations if (fid := _targeted(op, False))]

    for scene in _scene_nodes(after):
        raw = scene.get("кадры")
        if not isinstance(raw, list):
            continue
        active = list(shots_of({"shots": raw}) or [])
        active_mark = {id(item) for item in active}
        entity = scene_id(scene) or scene_number(scene) or ""
        for shot in raw:
            if not isinstance(shot, dict) or id(shot) in active_mark:
                continue
            fid = frame_id(shot) or ""
            path = _shot_path(entity, fid)
            if _marked_qc(shot) or _ops_target(operations, fid):
                _add(violations, seen, "inactive_shot_used", path, fid)
        for shot in raw:
            if not isinstance(shot, dict) or id(shot) not in active_mark:
                continue
            fid = frame_id(shot) or ""
            if fid and fid in before_inactive and fid not in activated:
                _add(
                    violations,
                    seen,
                    "inactive_shot_revived",
                    _shot_path(entity, fid),
                    fid,
                )
        scene_text = _scene_text(scene)
        joined = text_of(" ".join(_shot_text(shot) for shot in active if _shot_text(shot)))
        if scene_text and joined != scene_text:
            _add(violations, seen, "vo_on_inactive", f"scenes/{entity}/закадр" if entity else "закадр", entity)
        if deactivated and scene_text and not active:
            ids = {frame_id(shot) for shot in raw if isinstance(shot, dict)}
            for fid in deactivated:
                if fid in ids:
                    _add(
                        violations,
                        seen,
                        "last_active_deactivated",
                        _shot_path(entity, fid),
                        fid,
                    )
    return sorted_violations(violations)


def _scene_nodes(side: Any) -> list[dict[str, Any]]:
    if side is None:
        return []
    scenes = [scene for scene in collect_scenes(side) if isinstance(scene.get("кадры"), list)]
    if scenes:
        return scenes
    if isinstance(side, dict):
        for key in ("кадры", "shots", "frames"):
            if isinstance(side.get(key), list):
                return [
                    {
                        "id": side.get("id") or "root",
                        "номер": side.get("номер"),
                        "закадр": side.get("закадр") or side.get("voiceover") or "",
                        "кадры": side[key],
                    }
                ]
    return []


def _inactive_ids(scenes: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for scene in scenes:
        raw = scene.get("кадры")
        if not isinstance(raw, list):
            continue
        for shot in raw:
            if not isinstance(shot, dict) or shot_counts_as_active(shot):
                continue
            fid = frame_id(shot)
            if fid:
                found.add(fid)
    return found


def _scene_text(scene: dict[str, Any]) -> str:
    return text_of(
        scene.get("закадр") or scene.get("voiceover") or scene.get("vo") or scene.get("vo_cell_full")
    )


def _shot_text(shot: Any) -> str:
    if not isinstance(shot, dict):
        return ""
    return text_of(shot.get("закадр") or shot.get("voiceover") or shot.get("vo"))


def _shot_path(entity: str, fid: str) -> str:
    if entity and fid:
        return f"scenes/{entity}/кадры/{fid}"
    if entity:
        return f"scenes/{entity}/кадры"
    return f"кадры/{fid}" if fid else "кадры"


def _marked_qc(shot: dict[str, Any]) -> bool:
    for key in ("qc", "QC", "render", "рендер"):
        if shot.get(key):
            return True
    return False


def _verb(op: dict[str, Any]) -> str:
    return str(op.get("op") or op.get("action") or op.get("type") or "").strip().casefold()


def _op_id(op: dict[str, Any]) -> str:
    for key in ("shot_id", "frame_id", "id", "target"):
        if op.get(key) not in (None, ""):
            return str(op[key])
    return ""


def _flag(op: dict[str, Any]) -> bool | None:
    fields = op.get("fields")
    if isinstance(fields, dict) and "активен" in fields:
        if fields["активен"] is False:
            return False
        if fields["активен"] is True:
            return True
    verb = _verb(op)
    if verb in _ACTIVATE_VERBS:
        return True
    if verb in _DEACTIVATE_VERBS:
        return False
    return None


def _targeted(op: dict[str, Any], active: bool) -> str:
    if _flag(op) is not active:
        return ""
    return _op_id(op)


def _ops_target(ops: list[dict[str, Any]], fid: str) -> bool:
    if not fid:
        return False
    for op in ops:
        if _verb(op) in _QC_VERBS and _op_id(op) == fid:
            return True
    return False


def _add_warn(
    violations: list[Violation],
    seen: set[tuple[str, str, str]],
    code: str,
    path: str,
    entity_id: str,
) -> None:
    key = (code, path, entity_id)
    if key in seen:
        return
    seen.add(key)
    violations.append(Violation(code=code, severity="warn", path=path, id=entity_id))


def _add(
    violations: list[Violation],
    seen: set[tuple[str, str, str]],
    code: str,
    path: str,
    entity_id: str,
) -> None:
    key = (code, path, entity_id)
    if key in seen:
        return
    seen.add(key)
    violations.append(Violation(code=code, severity="error", path=path, id=entity_id))
