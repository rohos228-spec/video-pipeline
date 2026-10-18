"""Соседей под R-MERGE-TRIVIAL остаться не должно (п.5)."""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.grammar import active_shots, merge_notes, shots_of
from app.orchestrator.validators.result import make_result

_NAME = "shot_merge"


def check(project_id: int, context: dict[str, Any]) -> Any:
    del project_id
    shots = active_shots(shots_of(context))
    if not shots:
        return make_result(
            name=_NAME,
            passed=True,
            blocking=False,
            details={"checked": False, "reason": "no_shots"},
        )
    notes = merge_notes(shots)
    if notes:
        return make_result(
            name=_NAME,
            passed=False,
            blocking=True,
            details={"violations": notes},
        )
    return make_result(name=_NAME, passed=True, blocking=False, details={"violations": []})
