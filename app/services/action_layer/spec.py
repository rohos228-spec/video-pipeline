"""Каталог действий: ActionSpec, CostEstimate и глобальный ActionRegistry.

Модель/планировщик только ВЫБИРАЕТ id и заполняет параметры. Риск, стоимость и
предусловия задаёт код действия, а не запрос.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.services.action_layer import schema as schema_mod

RISK_READ = "read"
RISK_LOCAL_SAFE = "local_safe"
RISK_PAID = "paid"
RISK_DESTRUCTIVE = "destructive"
RISK_TIERS = (RISK_READ, RISK_LOCAL_SAFE, RISK_PAID, RISK_DESTRUCTIVE)
_RISK_RANK = {name: i for i, name in enumerate(RISK_TIERS)}

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class ActionRegistryError(ValueError):
    """Некорректная регистрация действия (дубликат, неверные флаги, схема)."""


@dataclass(frozen=True)
class CostEstimate:
    """Оценка в кредитах. low/high = None означает «неизвестно», а не 0."""
    low: float | None
    high: float | None
    basis: str = ""

    @property
    def known(self) -> bool:
        return self.low is not None and self.high is not None

    @classmethod
    def free(cls, basis: str = "no paid calls") -> "CostEstimate":
        return cls(0.0, 0.0, basis)

    @classmethod
    def unknown(cls, basis: str = "no estimate available") -> "CostEstimate":
        return cls(None, None, basis)

    def to_json(self) -> dict:
        return {"low": self.low, "high": self.high, "basis": self.basis, "known": self.known}


@dataclass(frozen=True)
class Precondition:
    """check(ctx, params) -> None если выполнено, иначе причина (строка)."""
    id: str
    description_ru: str
    check: Callable[[Any, dict], Awaitable[str | None]]


@dataclass
class JobState:
    """Состояние фоновой задачи действия (для async-исполнителей)."""
    done: bool
    ok: bool = True
    progress: float | None = None
    note: str = ""
    result: dict | None = None
    error: str | None = None
    credits_spent: float | None = None


Executor = Callable[[Any, dict], Awaitable[dict]]
Estimator = Callable[[dict], CostEstimate | None]
Poller = Callable[[Any, dict, dict], Awaitable[JobState]]
Stopper = Callable[[Any, dict], Awaitable[None]]


@dataclass
class ActionSpec:
    id: str
    title_ru: str
    description_ru: str
    params_schema: dict
    executor: Executor
    risk: str = RISK_READ
    spends_money: bool = False
    destructive: bool = False
    reversible: bool = False
    # Повтор после сбоя безвреден (значение выставляется, а не переключается).
    idempotent: bool = False
    preconditions: tuple[Precondition, ...] = ()
    estimator: Estimator | None = None
    destructive_effects: Callable[[dict], str] | None = None
    # Фоновое действие: executor стартует задачу, poll опрашивает, stop останавливает.
    poll: Poller | None = None
    stop: Stopper | None = None
    poll_interval_s: float = 2.0
    scope: str = "project"
    tags: tuple[str, ...] = ()

    @property
    def is_job(self) -> bool:
        return self.poll is not None

    @property
    def needs_approval(self) -> bool:
        return self.spends_money or self.destructive

    def fingerprint(self) -> str:
        """Хэш свойств, влияющих на безопасность (входит в хэш плана)."""
        raw = json.dumps(
            {"id": self.id, "risk": self.risk, "spends": self.spends_money,
             "destructive": self.destructive, "schema": self.params_schema},
            sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def estimate(self, params: dict) -> CostEstimate:
        if not self.spends_money:
            return CostEstimate.free()
        try:
            est = self.estimator(params) if self.estimator else None
        except Exception as exc:  # noqa: BLE001 - оценка не должна ронять preview
            return CostEstimate.unknown(f"estimator error: {type(exc).__name__}")
        return est if isinstance(est, CostEstimate) else CostEstimate.unknown()

    def to_json(self) -> dict:
        return {
            "id": self.id, "title_ru": self.title_ru, "description_ru": self.description_ru,
            "params_schema": self.params_schema, "risk": self.risk,
            "spends_money": self.spends_money, "destructive": self.destructive,
            "reversible": self.reversible, "idempotent": self.idempotent,
            "needs_approval": self.needs_approval, "async_job": self.is_job,
            "supports_stop": self.stop is not None, "scope": self.scope,
            "tags": list(self.tags),
            "preconditions": [{"id": p.id, "description_ru": p.description_ru}
                              for p in self.preconditions],
        }


def max_risk(risks: list[str]) -> str:
    return max(risks, key=lambda r: _RISK_RANK[r]) if risks else RISK_READ


def _validate_spec(spec: ActionSpec) -> None:
    if not isinstance(spec.id, str) or not _ID_RE.match(spec.id):
        raise ActionRegistryError(f"invalid action id {spec.id!r} (use 'area.name')")
    if not (spec.title_ru or "").strip() or not (spec.description_ru or "").strip():
        raise ActionRegistryError(f"{spec.id}: title_ru and description_ru are required")
    if spec.risk not in RISK_TIERS:
        raise ActionRegistryError(f"{spec.id}: unknown risk tier {spec.risk!r}")
    if spec.destructive != (spec.risk == RISK_DESTRUCTIVE):
        raise ActionRegistryError(f"{spec.id}: destructive flag must match risk tier 'destructive'")
    if spec.spends_money and spec.risk not in (RISK_PAID, RISK_DESTRUCTIVE):
        raise ActionRegistryError(f"{spec.id}: spends_money requires risk 'paid' or 'destructive'")
    if spec.risk == RISK_PAID and not spec.spends_money:
        raise ActionRegistryError(f"{spec.id}: risk 'paid' requires spends_money=True")
    if spec.spends_money and spec.estimator is None:
        raise ActionRegistryError(f"{spec.id}: paid action needs a cost estimator")
    if not inspect.iscoroutinefunction(spec.executor):
        raise ActionRegistryError(f"{spec.id}: executor must be an async function")
    if spec.poll is not None and not inspect.iscoroutinefunction(spec.poll):
        raise ActionRegistryError(f"{spec.id}: poll must be an async function")
    if spec.stop is not None and not inspect.iscoroutinefunction(spec.stop):
        raise ActionRegistryError(f"{spec.id}: stop must be an async function")
    for pre in spec.preconditions:
        if not inspect.iscoroutinefunction(pre.check):
            raise ActionRegistryError(f"{spec.id}: precondition {pre.id} must be async")
    schema = spec.params_schema
    problems = schema_mod.check_schema(schema)
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        problems.append("root schema must be type=object with additionalProperties=false")
    if problems:
        raise ActionRegistryError(f"{spec.id}: bad params_schema: {'; '.join(problems)}")


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> ActionSpec:
        _validate_spec(spec)
        if spec.id in self._actions:
            raise ActionRegistryError(f"duplicate action id {spec.id!r}")
        self._actions[spec.id] = spec
        return spec

    def register_many(self, specs: list[ActionSpec]) -> None:
        """Все или ничего: при ошибке ни одно действие пачки не остаётся."""
        added: list[str] = []
        try:
            for spec in specs:
                self.register(spec)
                added.append(spec.id)
        except ActionRegistryError:
            for action_id in added:
                self._actions.pop(action_id, None)
            raise

    def unregister(self, action_id: str) -> None:
        self._actions.pop(action_id, None)

    def get(self, action_id: str) -> ActionSpec:
        try:
            return self._actions[action_id]
        except KeyError:
            raise KeyError(f"unknown action {action_id!r}") from None

    def find(self, action_id: str) -> ActionSpec | None:
        return self._actions.get(action_id)

    def list(self, *, risk: str | None = None, prefix: str | None = None) -> list[ActionSpec]:
        out = [s for s in self._actions.values()
               if (risk is None or s.risk == risk)
               and (prefix is None or s.id.startswith(prefix))]
        return sorted(out, key=lambda s: s.id)

    def validate_params(self, action_id: str, params: Any) -> list[str]:
        spec = self.get(action_id)
        if not isinstance(params, dict):
            return ["$: params must be an object"]
        return schema_mod.validate(spec.params_schema, params)

    def __contains__(self, action_id: str) -> bool:
        return action_id in self._actions

    def __len__(self) -> int:
        return len(self._actions)


registry = ActionRegistry()
_builtin_specs: list | None = None


def _builtin_spec_list() -> list:
    global _builtin_specs
    if _builtin_specs is None:
        from app.services.action_layer.builtin_group import register_group_actions
        from app.services.action_layer.builtin_montage import register_montage_actions

        staging = ActionRegistry()
        register_montage_actions(staging)
        register_group_actions(staging)
        _builtin_specs = staging.list()
    return _builtin_specs


def ensure_builtin_actions() -> None:
    """Идемпотентная регистрация встроенных действий: добавляет только те, которых ещё нет."""
    for spec in _builtin_spec_list():
        if registry.find(spec.id) is None:
            registry.register(spec)
