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
    *,
    run_id: str | None = None,
    task_id: int | str | None = None,
) -> ValidatorResult:
    """``validators.run``. Корутину ждём, синхронный ``run`` — в ``asyncio.to_thread``.

    В context всегда попадают непустые ``run_id`` и ``task_id`` — из kwargs
    или из самого context. Пустые значения до валидатора не доходят:
    live ``spend_guard`` иначе блокирует с ``reason=missing_run_id``.
    """
    payload = _with_run_identity(context, run_id=run_id, task_id=task_id)
    fn = runner.run
    if inspect.iscoroutinefunction(fn):
        result = await fn(name, project_id, payload)
    else:
        result = await asyncio.to_thread(fn, name, project_id, payload)
        if inspect.isawaitable(result):
            result = await result
    return _coerce_result(result)


def _with_run_identity(
    context: dict[str, Any],
    *,
    run_id: str | None,
    task_id: int | str | None,
) -> dict[str, Any]:
    payload = dict(context)
    bound_run = run_id if run_id is not None else payload.get("run_id")
    bound_task = task_id if task_id is not None else payload.get("task_id")
    if not isinstance(bound_run, str) or not bound_run.strip():
        raise ValueError("validator context requires run_id")
    if isinstance(bound_task, str):
        bound_task = bound_task.strip()
    if bound_task is None or bound_task == "":
        raise ValueError("validator context requires task_id")
    payload["run_id"] = bound_run.strip()
    payload["task_id"] = bound_task
    return payload


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
