"""Единый прогон валидаторов сохранности по (before, after, ops)."""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.no_deletions import (
    check_characters,
    check_frames,
    check_scenes,
)
from app.orchestrator.validators.result import make_result
from app.orchestrator.validators.shot_activity import check_active_shots
from app.orchestrator.validators.violations import Violation, sorted_violations

_NAME = "preservation"


def collect_violations(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Все проверки сохранности. Только чтение, стабильный порядок кодов."""

    found: list[Violation] = []
    found.extend(check_scenes(before, after, ops))
    found.extend(check_frames(before, after, ops))
    found.extend(check_characters(before, after, ops))
    found.extend(check_active_shots(before, after, ops))
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
