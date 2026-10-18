"""Единый прогон валидаторов сохранности по (before, after, ops)."""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.entities import (
    collect_scenes,
    frame_id,
    frame_is_exempt,
    scene_id,
    scene_kadry,
    scene_number,
)
from app.orchestrator.validators.frame_links import check_frame_links
from app.orchestrator.validators.frames_cleared import check_frames_cleared
from app.orchestrator.validators.no_deletions import (
    check_characters,
    check_frames,
    check_scenes,
)
from app.orchestrator.validators.result import make_result
from app.orchestrator.validators.shot_activity import check_active_shots, check_kadry_invalid
from app.orchestrator.validators.shot_dropped import (
    check_marked_shot_removed,
    check_shot_dropped,
)
from app.orchestrator.validators.violations import Violation, sorted_violations

_NAME = "preservation"


class PreservationRejected(Exception):
    """Отказ записи: среди нарушений есть error.

    ``violations`` — полный список, включая warn. Вызывающий не коммитит.
    """

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = list(violations)
        text = ", ".join(
            f"{item.code}:{item.id or item.path}"
            for item in self.violations
            if item.severity == "error"
        )
        super().__init__(f"сохранность: {text}")


def run_preservation_validators(
    before: Any,
    after: Any,
    ops: Any = None,
    *,
    mode: str = "reject",
) -> list[Violation]:
    """Прогон сохранности перед записью.

    ``mode="reject"`` (по умолчанию) — любой error поднимает
    ``PreservationRejected``, ``after`` не меняется.
    ``mode="soft"`` — пропавший живой шот дописывается в ``кадры[]`` с
    ``активен: False`` и warn ``kept_inactive``. На id этого шота не
    отказывают ``frame_deleted``, ``shot_dropped``, ``vo_on_inactive`` и
    ``inactive_shot_used``. ``vo_on_inactive`` с id сцены soft не гасит.
    Другой error по-прежнему отказ.
    """

    if mode not in {"reject", "soft"}:
        raise ValueError(f"mode должен быть reject или soft, не {mode!r}")
    violations = collect_violations(before, after, ops)
    if mode == "soft":
        restored = _keep_missing_shots(before, after)
        if restored:
            parked = {fid for _, fid in restored}
            absorbed = {
                "frame_deleted",
                "shot_dropped",
                "vo_on_inactive",
                "inactive_shot_used",
            }
            violations = [
                item
                for item in collect_violations(before, after, ops)
                if not (item.id in parked and item.code in absorbed)
            ]
            violations.extend(
                Violation(code="kept_inactive", severity="warn", path=path, id=fid)
                for path, fid in restored
            )
            violations = sorted_violations(violations)
    if any(item.severity == "error" for item in violations):
        raise PreservationRejected(violations)
    return violations


def _keep_missing_shots(before: Any, after: Any) -> list[tuple[str, str]]:
    """Вернуть пропавшие живые кадры в список ``after`` с ``активен: False``."""

    if not isinstance(after, dict):
        return []
    after_by_id: dict[str, dict[str, Any]] = {}
    after_by_number: dict[str, dict[str, Any]] = {}
    for scene in collect_scenes(after):
        sid = scene_id(scene)
        number = scene_number(scene)
        if sid and sid not in after_by_id:
            after_by_id[sid] = scene
        if number and number not in after_by_number:
            after_by_number[number] = scene
    restored: list[tuple[str, str]] = []
    for scene in collect_scenes(before):
        status, frames = scene_kadry(scene)
        if status != "list":
            continue
        sid = scene_id(scene)
        number = scene_number(scene)
        match = after_by_id.get(sid or "") if sid else None
        if match is None and number:
            match = after_by_number.get(number)
        if match is None:
            continue
        after_status, after_frames = scene_kadry(match)
        if after_status != "list" or not isinstance(after_frames, list):
            continue
        have = {fid for item in after_frames if (fid := frame_id(item))}
        entity = sid or number or ""
        for frame in frames:
            if frame_is_exempt(frame):
                continue
            fid = frame_id(frame)
            if not fid or fid in have:
                continue
            kept = dict(frame) if isinstance(frame, dict) else {"id": fid}
            kept["активен"] = False
            after_frames.append(kept)
            have.add(fid)
            path = f"scenes/{entity}/кадры/{fid}" if entity else f"кадры/{fid}"
            restored.append((path, fid))
    return restored


def collect_violations(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Все проверки сохранности. Только чтение, стабильный порядок кодов."""

    found: list[Violation] = []
    found.extend(check_scenes(before, after, ops))
    found.extend(check_frames(before, after, ops))
    found.extend(check_characters(before, after, ops))
    found.extend(check_frame_links(before, after, ops))
    found.extend(check_kadry_invalid(before, after, ops))
    found.extend(check_active_shots(before, after, ops))
    found.extend(check_shot_dropped(before, after, ops))
    found.extend(check_marked_shot_removed(before, after, ops))
    found.extend(check_frames_cleared(before, after, ops))
    return sorted_violations(found)


def check(project_id: int, context: dict[str, Any]) -> Any:
    del project_id
    violations = collect_violations(
        context.get("before"),
        context.get("after"),
        context.get("ops"),
    )
    errors = [item for item in violations if item.severity == "error"]
    return make_result(
        name=_NAME,
        passed=not errors,
        blocking=bool(errors),
        details={"violations": [item.as_dict() for item in violations]},
    )
