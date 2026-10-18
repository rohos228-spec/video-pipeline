"""Вход plan_sanity без остального стека R0a.

``run(name, project_id, context)`` здесь регистрирует только ``plan_sanity``.
``no_deletions``, ``preservation``, ``spend_guard`` и прочие проверки в main
ещё не влиты и в этот модуль не импортируются.
"""

from __future__ import annotations

from typing import Any

from . import plan_sanity
from .result import VALIDATOR_CONTEXT_DRY_RUN, make_result

__all__ = ["VALIDATOR_CONTEXT_DRY_RUN", "run"]


def run(name: str, project_id: int, context: dict[str, Any] | None = None) -> Any:
    """Синхронный вызов одной проверки. Контекст копируется, план не заменяется."""

    payload = dict(context or {})
    payload.setdefault("project_id", project_id)
    if name != "plan_sanity":
        return make_result(
            name=name,
            passed=False,
            blocking=False,
            details={"reason": "unknown_validator"},
        )
    try:
        return plan_sanity.check(project_id, payload)
    except Exception as exc:  # noqa: BLE001 — ошибка валидатора не роняет процесс
        return make_result(
            name=name,
            passed=False,
            blocking=True,
            details={"reason": "validator_error", "error": type(exc).__name__},
        )
