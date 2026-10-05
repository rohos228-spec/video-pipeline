"""Потолок трат (кредиты) на сессию плана и проверка аварийного стопа.

Резерв под оценку шага (верхняя граница) делается одним атомарным INSERT ...
SELECT до запуска шага; после шага сверка с фактом дописывается отдельной
строкой журнала (журнал append-only). Неизвестная оценка платного шага =
отказ (fail-closed), а не 0.
"""
from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import func, select, text

from app.services import kill_switch
from app.services.action_layer import store
from app.services.action_layer.spec import CostEstimate

KILL_SWITCH_REASON = kill_switch.REASON


class BudgetError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def session_cap() -> float:
    from app.settings import settings
    return float(settings.action_layer_session_cap_credits)


def session_key(plan_id: str) -> str:
    return f"plan:{plan_id}"


async def spent(session, key: str) -> float:
    total = await session.scalar(select(func.coalesce(func.sum(store.ActionSpend.credits), 0.0))
                                 .where(store.ActionSpend.session_key == key))
    return float(total or 0.0)


async def remaining(session, key: str, cap: float | None = None) -> float:
    cap = session_cap() if cap is None else cap
    return cap - await spent(session, key)


def preview_budget(cost: dict, cap: float | None = None, already_spent: float = 0.0) -> dict:
    """Помещается ли оценка плана в потолок (для preview/approve)."""
    cap = session_cap() if cap is None else cap
    free = cap - already_spent
    fits = bool(cost.get("known", True)) and float(cost.get("high") or 0.0) <= free + 1e-9
    return {"cap_credits": cap, "remaining_credits": free, "fits": fits}


async def reserve(session, *, key: str, plan_id: str, step_index: int,
                  estimate: CostEstimate, cap: float | None = None) -> float:
    """Резервирует верхнюю оценку или бросает BudgetError. Возвращает резерв."""
    if not estimate.known:
        raise BudgetError("estimate_unknown", "cost estimate unknown for a paid step")
    amount = float(estimate.high)
    if amount < 0 or math.isnan(amount) or math.isinf(amount):
        raise BudgetError("estimate_invalid", "invalid cost estimate")
    cap = session_cap() if cap is None else cap
    result = await session.execute(text(
        "INSERT INTO action_layer_spend (session_key, plan_id, step_index, kind, credits, created_at) "
        "SELECT :k, :p, :i, 'reserve', :c, :now WHERE "
        "COALESCE((SELECT SUM(credits) FROM action_layer_spend WHERE session_key = :k), 0) + :c <= :cap"),
        {"k": key, "p": plan_id, "i": step_index, "c": amount, "cap": cap,
         "now": datetime.utcnow()})
    if result.rowcount != 1:
        left = await remaining(session, key, cap)
        raise BudgetError("budget_exceeded",
                          f"estimate {amount:g} exceeds remaining cap {left:g} credits")
    await _spend_reserve(session, plan_id, amount)
    return amount


async def _plan_project(session, plan_id: str):
    row = await session.get(store.ActionPlanContext, plan_id)
    return row.project_id if row is not None else None


async def _spend_reserve(session, plan_id: str, credits: float) -> None:
    """Local USD caps (daily/project/run) in addition to the per-plan credit cap."""
    from app.services import spend_guard
    from app.services.kie_catalog import CREDIT_USD
    try:
        await spend_guard.reserve(session, project_id=await _plan_project(session, plan_id),
                                  run_id=f"plan:{plan_id}", estimate_usd=credits * CREDIT_USD,
                                  source="action_layer", credits=credits, track=False)
    except spend_guard.SpendCapExceeded as exc:
        raise BudgetError(exc.code, str(exc)) from exc


async def settle(session, *, key: str, plan_id: str, step_index: int,
                 reserved: float, actual: float | None) -> None:
    """Сверка резерва с фактом; факт неизвестен — резерв остаётся (консервативно)."""
    if actual is None or actual < 0 or math.isnan(actual):
        return
    delta = float(actual) - float(reserved)
    if delta:
        from app.services import spend_guard
        from app.services.kie_catalog import CREDIT_USD
        await spend_guard.settle(session, project_id=await _plan_project(session, plan_id),
                                 run_id=f"plan:{plan_id}", reserved_usd=float(reserved) * CREDIT_USD,
                                 actual_usd=float(actual) * CREDIT_USD, source="action_layer",
                                 credits_delta=delta)
        session.add(store.ActionSpend(session_key=key, plan_id=plan_id, step_index=step_index,
                                      kind="settle", credits=delta))


async def guard_kill_switch(session) -> None:
    """Бросает KillSwitchActive до любого шага (и чтения тоже)."""
    await kill_switch.guard(session)
