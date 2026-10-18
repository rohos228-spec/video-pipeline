"""Перед диалогом двух говорящих должен стоять общий план (п.6)."""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.grammar import active_shots, shots_of, wide_notes
from app.orchestrator.validators.result import make_result

_NAME = "wide_before_dialogue"


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
    notes = wide_notes(shots)
    if notes:
        return make_result(
            name=_NAME,
            passed=False,
            blocking=True,
            details={"violations": notes},
        )
    return make_result(name=_NAME, passed=True, blocking=False, details={"violations": []})
