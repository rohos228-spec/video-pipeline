"""Программные валидаторы оркестратора (§7.2).

``run(name, project_id, context) -> ValidatorResult``. ``name="all"`` гоняет
зарегистрированные проверки и сворачивает их в один результат. ``vo_length``
зарезервирован под R0a: если модуля нет, импорт пакета не падает, а проверка
считается недоступной и в сводке ``all`` не валит ``pass``.

``context[VALIDATOR_CONTEXT_DRY_RUN]`` (ключ ``dry_run``) — режим до критика.
Валидаторы и в этом режиме только читают: своих записей у них нет.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from . import no_deletions, plan_sanity, preservation, shot_merge, spend_guard, wide_before_dialogue
from .result import (
    VALIDATOR_CONTEXT_DRY_RUN,
    dry_run_key,
    is_dry_run,
    make_result,
    reset_contract_cache,
    using_contracts,
    validator_budget_exhausted,
)

Checker = Callable[[int, dict[str, Any]], Any]

_ORDER = (
    "vo_length",
    "no_deletions",
    "wide_before_dialogue",
    "shot_merge",
    "spend_guard",
    "plan_sanity",
    "preservation",
)

_CHECKS: dict[str, Checker] = {
    "no_deletions": no_deletions.check,
    "wide_before_dialogue": wide_before_dialogue.check,
    "shot_merge": shot_merge.check,
    "spend_guard": spend_guard.check,
    "plan_sanity": plan_sanity.check,
    "preservation": preservation.check,
}

VO_LENGTH = "vo_length"


def registered_names() -> tuple[str, ...]:
    return _ORDER


def vo_length_available() -> bool:
    return _vo_length_checker() is not None


def run(name: str, project_id: int, context: dict[str, Any] | None = None) -> Any:
    """Синхронный ``ValidatorRunner.run`` (#350 ``406a0cfb``)."""

    payload = dict(context or {})
    payload.setdefault("project_id", project_id)
    if name == "all":
        return _run_all(project_id, payload)
    return _run_one(name, project_id, payload)


def _run_all(project_id: int, context: dict[str, Any]) -> Any:
    results = []
    unavailable: list[str] = []
    for validator_name in _ORDER:
        if validator_name == VO_LENGTH and not vo_length_available():
            unavailable.append(validator_name)
            results.append(_unavailable(validator_name))
            continue
        results.append(_run_one(validator_name, project_id, context))
    considered = [item for item in results if item.name not in unavailable]
    blocking = any(bool(item.blocking) and not bool(item.passed) for item in considered)
    passed = all(bool(item.passed) for item in considered) if considered else True
    return make_result(
        name="all",
        passed=passed,
        blocking=blocking,
        details={
            "results": [_dump(item) for item in results],
            "unavailable": unavailable,
            "dry_run": is_dry_run(context),
        },
    )


def _run_one(name: str, project_id: int, context: dict[str, Any]) -> Any:
    if name == VO_LENGTH:
        checker = _vo_length_checker()
        if checker is None:
            return _unavailable(name)
    else:
        checker = _CHECKS.get(name)
    if checker is None:
        return make_result(
            name=name,
            passed=False,
            blocking=False,
            details={"reason": "unknown_validator"},
        )
    try:
        result = checker(project_id, context)
    except Exception as exc:  # noqa: BLE001 — ошибка валидатора не роняет процесс
        return make_result(
            name=name,
            passed=False,
            blocking=True,
            details={"reason": "validator_error", "error": type(exc).__name__},
        )
    return result


def _unavailable(name: str) -> Any:
    return make_result(
        name=name,
        passed=False,
        blocking=False,
        details={"available": False, "reason": "unavailable"},
    )


def _vo_length_checker() -> Checker | None:
    try:
        module = importlib.import_module("app.orchestrator.validators.vo_length")
    except ModuleNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        return None
    check = getattr(module, "check", None)
    if callable(check):
        return check
    lengths = getattr(module, "check_vo_lengths", None)
    if not callable(lengths):
        return None

    def _adapt(project_id: int, context: dict[str, Any]) -> Any:
        del project_id
        fragments = context.get("fragments")
        if fragments is None:
            fragments = context.get("vo") or []
        violations = list(lengths(fragments) or [])
        return make_result(
            name=VO_LENGTH,
            passed=not violations,
            blocking=bool(violations),
            details={"violations": violations, "available": True},
        )

    return _adapt


def _dump(result: Any) -> dict[str, Any]:
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True)
    return {
        "name": getattr(result, "name", ""),
        "pass": bool(getattr(result, "passed", False)),
        "details": getattr(result, "details", ""),
        "blocking": bool(getattr(result, "blocking", False)),
    }


__all__ = [
    "VALIDATOR_CONTEXT_DRY_RUN",
    "VO_LENGTH",
    "dry_run_key",
    "is_dry_run",
    "make_result",
    "registered_names",
    "reset_contract_cache",
    "run",
    "using_contracts",
    "validator_budget_exhausted",
    "vo_length_available",
]
