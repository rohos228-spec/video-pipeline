"""Тонкий shim ``ValidatorResult``, пока ``contracts.py`` нет в main.

Поля совпадают с WS3: база #346 ``fab1b126``, PR #350 HEAD ``406a0cfb``
(``cursor/ws3-agents-r3-3c7d``). ``name``, ``passed`` (JSON-алиас ``pass``),
``details``, ``blocking``. ``ValidatorRunner.run`` в этом SHA синхронный:
``run(name, project_id, context) -> ValidatorResult``. Ключ dry-run —
``VALIDATOR_CONTEXT_DRY_RUN`` (``"dry_run"``): супервизор кладёт
``context["dry_run"]=True``. Когда ``app.orchestrator.contracts``
импортируется, берём класс, константу и ``validator_budget_exhausted``
оттуда. Пока #350 не в main, остаётся fallback с теми же полями и той же
проверкой: ``blocking`` и ``details.reason == "budget_exhausted"``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Совпадает с contracts PR #350. Не менять строку: супервизор кладёт её в context.
_SHIM_DRY_RUN = "dry_run"

_cache: tuple[type, str, bool] | None = None


class _ShimValidatorResult(BaseModel):
    """Локальная копия контракта. После появления contracts не конструируется."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    passed: bool = Field(default=False, alias="pass", serialization_alias="pass")
    details: Any = ""
    blocking: bool = False


def reset_contract_cache() -> None:
    """Сбросить привязку. Нужно тестам, которые подсовывают contracts в sys.modules."""

    global _cache
    _cache = None


def resolve_contract() -> tuple[type, str, bool]:
    """Вернуть (класс результата, ключ dry-run, взят_ли_из_contracts)."""

    global _cache
    if _cache is not None:
        return _cache
    cls: type = _ShimValidatorResult
    dry = _SHIM_DRY_RUN
    using = False
    try:
        from app.orchestrator import contracts as contracts
    except Exception:  # noqa: BLE001 — contracts ещё нет или модуль не собран
        contracts = None
    if contracts is not None and hasattr(contracts, "ValidatorResult"):
        cls = contracts.ValidatorResult
        dry = str(getattr(contracts, "VALIDATOR_CONTEXT_DRY_RUN", _SHIM_DRY_RUN))
        using = True
    _cache = (cls, dry, using)
    return _cache


def using_contracts() -> bool:
    return resolve_contract()[2]


def validator_result_type() -> type:
    return resolve_contract()[0]


def dry_run_key() -> str:
    return resolve_contract()[1]


def is_dry_run(context: dict[str, Any] | None) -> bool:
    if not context:
        return False
    return bool(context.get(dry_run_key()))


def make_result(
    *,
    name: str,
    passed: bool,
    details: Any = "",
    blocking: bool = False,
) -> Any:
    cls = validator_result_type()
    return cls(name=name, passed=passed, details=details, blocking=blocking)


_BUDGET_REASON = "budget_exhausted"


def validator_budget_exhausted(result: Any) -> bool:
    """Та же проверка, что ``contracts.validator_budget_exhausted`` (#350 ``406a0cfb``).

    Супервизор по ней переводит задачу в ``waiting_approval``, а не в ``failed``.
    Пока contracts нет в main, работает локальная копия. Когда функция
    появляется в модуле, зовём её.
    """

    remote = _contracts_budget_helper()
    if remote is not None:
        return bool(remote(result))
    return budget_exhausted_fallback(result)


def budget_exhausted_fallback(result: Any) -> bool:
    """Копия хелпера #350: blocking и ``details.reason == budget_exhausted``.

    ``details`` — dict или объект с полем ``reason``.
    """

    if not bool(getattr(result, "blocking", False)):
        return False
    details = getattr(result, "details", None)
    reason = details.get("reason") if isinstance(details, dict) else getattr(details, "reason", None)
    return reason == _BUDGET_REASON


def _contracts_budget_helper() -> Any:
    try:
        from app.orchestrator import contracts as contracts
    except Exception:  # noqa: BLE001 — #350 ещё не в main
        return None
    helper = getattr(contracts, "validator_budget_exhausted", None)
    return helper if callable(helper) else None


# Публичные имена. Класс перечитывается через resolve_contract, это значение
# на момент импорта: либо shim, либо contracts, если он уже был в пути.
ValidatorResult = validator_result_type()
VALIDATOR_CONTEXT_DRY_RUN = dry_run_key()
