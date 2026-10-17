"""Вызов ``validators.run``: синхронный контракт WS1 и async-мок.

Супервизор (план до критика и проверки шага) ходит только сюда.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from app.orchestrator.contracts import ValidatorResult


async def run_validator(
    runner: Any,
    name: str,
    project_id: int,
    context: dict[str, Any],
) -> ValidatorResult:
    """``validators.run``. Корутину ждём, синхронный ``run`` — в ``asyncio.to_thread``."""
    fn = runner.run
    if inspect.iscoroutinefunction(fn):
        result = await fn(name, project_id, context)
    else:
        result = await asyncio.to_thread(fn, name, project_id, context)
        if inspect.isawaitable(result):
            result = await result
    return _coerce_result(result)


def _coerce_result(result: Any) -> ValidatorResult:
    if isinstance(result, ValidatorResult):
        return result
    if isinstance(result, dict):
        return ValidatorResult.model_validate(result)
    passed = getattr(result, "passed", None)
    if passed is None:
        passed = getattr(result, "pass", False)
    return ValidatorResult(
        name=str(getattr(result, "name", "") or ""),
        passed=bool(passed),
        details=getattr(result, "details", ""),
        blocking=bool(getattr(result, "blocking", False)),
    )
