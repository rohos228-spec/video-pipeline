"""Тонкий shim ``ValidatorResult``.

Поля совпадают с WS3: #346 ``e4c3745b``, #350 ``19e803df``. ``name``,
``passed`` (JSON-алиас ``pass``), ``details``, ``blocking``.
``ValidatorRunner.run`` синхронный: ``run(name, project_id, context) -> ValidatorResult``.
Ключ dry-run — ``VALIDATOR_CONTEXT_DRY_RUN`` (``"dry_run"``): супервизор кладёт
``context["dry_run"]=True``. Когда ``app.orchestrator.contracts`` импортируется,
берём оттуда класс результата и константу dry-run.

Проверка бюджета локальная. ``validator_budget_exhausted`` смотрит ``blocking``
и ``details.reason == "budget_exhausted"`` и не читает contracts. Контракт
WS3: ``blocking=True`` и ``details.reason``. ``budget_exhausted`` уводит задачу
в ``waiting_approval``, ``missing_run_id`` — в ``failed``.
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
    """Локальная проверка: ``blocking`` и ``details.reason == "budget_exhausted"``.

    ``details`` — dict или объект с полем ``reason``. Другой ``reason``
    (в том числе ``missing_run_id``) сюда не входит.
    """

    if not bool(getattr(result, "blocking", False)):
        return False
    details = getattr(result, "details", None)
    reason = details.get("reason") if isinstance(details, dict) else getattr(details, "reason", None)
    return reason == _BUDGET_REASON


# Публичные имена. Класс перечитывается через resolve_contract, это значение
# на момент импорта: либо shim, либо contracts, если он уже был в пути.
ValidatorResult = validator_result_type()
VALIDATOR_CONTEXT_DRY_RUN = dry_run_key()
