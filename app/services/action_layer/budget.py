"""Потолок трат (кредиты) на сессию плана и проверка аварийного стопа.

Резерв под оценку шага (верхняя граница) делается одним атомарным INSERT ...
SELECT до запуска шага; после шага сверка с фактом дописывается отдельной
строкой журнала (журнал append-only). Неизвестная оценка платного шага =
отказ (fail-closed), а не 0.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import func, select, text

from app.services import kill_switch
from app.services.action_layer import store
from app.services.action_layer.messages_ru import spend_cap_ru
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
        raise BudgetError("estimate_unknown", "У платного шага нет оценки стоимости: запуск без неё запрещён.")
    amount = float(estimate.high)
    if amount < 0 or math.isnan(amount) or math.isinf(amount):
        raise BudgetError("estimate_invalid", "Оценка стоимости некорректна.")
    cap = session_cap() if cap is None else cap
    # The step's paid work (and the money rows of this reserve) carry the plan's project, run and a request id.
    await _bind_plan_context(session, plan_id)
    try:
        result = await session.execute(text(
            "INSERT INTO action_layer_spend (session_key, plan_id, step_index, kind, credits, created_at) "
            "SELECT :k, :p, :i, 'reserve', :c, :now WHERE "
            "COALESCE((SELECT SUM(credits) FROM action_layer_spend WHERE session_key = :k), 0) + :c <= :cap"),
            {"k": key, "p": plan_id, "i": step_index, "c": amount, "cap": cap,
             "now": datetime.utcnow()})
        if result.rowcount != 1:
            left = await remaining(session, key, cap)
            raise BudgetError("budget_exceeded",
                              f"Оценка {amount:g} кр. больше остатка потолка расходов ({left:g} кр.).")
        await _spend_reserve(session, plan_id, amount, step_index=step_index)
    except BaseException:
        from app.services import request_context
        request_context.unbind_run(plan_run_id(plan_id))  # refused or failed: nothing is bound to the step
        raise
    return amount


async def _bind_plan_context(session, plan_id: str) -> None:
    """B02: from the reserve to the settle of a step, every native paid call of this task (regeneration, Kie, Outsee,
    ElevenLabs) is written with the plan's project, ``plan:<id>`` as run and a request id. Without it the native
    rows of a plan step had no project and an empty run, so the layer could not be matched with its own spend."""
    from app.services import request_context
    request_context.bind_run(plan_run_id(plan_id), await _plan_project(session, plan_id), "plan-")


def plan_run_id(plan_id: str) -> str:
    return session_key(plan_id)


@contextmanager
def plan_scope(plan_id: str, project_id: int | None):
    """Explicit form of the same binding (for a caller that can wrap the step in ``with``)."""
    from app.services import request_context
    with request_context.ensure_request_scope("plan-", run_id=plan_run_id(plan_id), project_id=project_id) as snap:
        yield snap


async def _plan_project(session, plan_id: str):
    row = await session.get(store.ActionPlanContext, plan_id)
    if row is not None and row.project_id is not None:
        return row.project_id
    plan = await session.get(store.ActionPlan, plan_id)  # план без контекста планировщика
    return plan.project_id if plan is not None else None


async def _spend_reserve(session, plan_id: str, credits: float, step_index: int | None = None) -> None:
    """Local USD caps (daily/project/run) in addition to the per-plan credit cap."""
    from app.services import spend_guard
    from app.services.kie_catalog import CREDIT_USD
    try:
        await spend_guard.reserve(session, project_id=await _plan_project(session, plan_id),
                                  run_id=f"plan:{plan_id}", estimate_usd=credits * CREDIT_USD,
                                  source="action_layer", credits=credits, track=False,
                                  ref=f"plan:{plan_id}#{step_index}" if step_index is not None else "")
    except spend_guard.SpendCapExceeded as exc:
        raise BudgetError(exc.code, spend_cap_ru(exc)) from exc


async def settle(session, *, key: str, plan_id: str, step_index: int,
                 reserved: float, actual: float | None,
                 native_counted: bool = False) -> None:
    """Сверка резерва с фактом; факт неизвестен - резерв остаётся (консервативно).

    ``native_counted=True``: факт уже записан в ``spend_ledger`` нативными путями
    (``media_native``), поэтому резерв слоя в общем журнале закрывается до нуля, а не до
    факта, иначе одна генерация считается дважды. Потолок плана (``action_layer_spend``)
    всегда сверяется с фактом.
    """
    from app.services import request_context
    rid = request_context.get_request_id()  # the rows below still belong to the step's request
    try:
        if actual is None or actual < 0 or math.isnan(actual) or math.isinf(actual):
            return
        delta = float(actual) - float(reserved)
        if not delta and not (native_counted and reserved):
            return
        from app.services import spend_guard
        from app.services.kie_catalog import CREDIT_USD
        ledger_actual = 0.0 if native_counted else float(actual)
        ledger_delta = ledger_actual - float(reserved)
        if ledger_delta:
            await spend_guard.settle(session, project_id=await _plan_project(session, plan_id),
                                     run_id=f"plan:{plan_id}", reserved_usd=float(reserved) * CREDIT_USD,
                                     actual_usd=ledger_actual * CREDIT_USD, source="action_layer",
                                     credits_delta=ledger_delta,
                                     # B03: native_counted closes the reserve to zero (the fact is already in the
                                     # native rows, so it is not a second spend); otherwise the executor's reported
                                     # amount is a fact (the action's own answer).
                                     basis="closes_reserve" if native_counted else "reported",
                                     ref=f"plan:{plan_id}#{step_index}", request_id=rid)
        if delta:
            session.add(store.ActionSpend(session_key=key, plan_id=plan_id, step_index=step_index,
                                          kind="settle", credits=delta))
    finally:
        request_context.unbind_run(plan_run_id(plan_id))  # end of the step: the binding of reserve() is released


_UNKNOWN_MARKS = ("cost_unknown", "write_failed")


async def measure_native_credits(session, *, project_id: int | None, since: datetime,
                                 run_id: str | None = None) -> float | None:
    """Факт трат проекта с момента ``since`` по строкам ``spend_ledger`` нативных путей.

    Источник ``action_layer`` (резервы самого слоя) не считается. ``None``: факт неизвестен
    (нет проекта, в журнале строка «цена неизвестна» или «дыра» записи), тогда резерв
    остаётся. Пустой журнал после завершения шага = 0: платный вызов без записи
    невозможен (запись пишет сам шов), но если запись потеряна, это видно как ``write_failed``.
    """
    if project_id is None:
        return None
    from app.services import spend_guard
    from app.services.kie_catalog import CREDIT_USD
    try:
        from app.services import media_spend
        await media_spend.drain()
    except Exception:  # noqa: BLE001 - ожидание фоновых записей необязательно
        pass
    await spend_guard.ensure_schema(session)
    q = select(spend_guard.SpendLedger).where(
        spend_guard.SpendLedger.project_id == project_id,
        spend_guard.SpendLedger.created_at >= since,
        spend_guard.SpendLedger.kind != "event",
        spend_guard.SpendLedger.source != "action_layer")
    if run_id:  # B02: rows of this plan's own run only (another tick of the project in the same minutes is not ours)
        q = q.where(spend_guard.SpendLedger.run_id == run_id)
    rows = (await session.scalars(q)).all()
    total = 0.0
    for r in rows:
        note = (r.note or "").lower()
        if any(m in note for m in _UNKNOWN_MARKS):
            return None
        total += float(r.credits) if r.credits else float(r.usd or 0.0) / CREDIT_USD
    return max(0.0, total)


async def native_unknown_cost_rows(session, *, project_id: int | None, since: datetime) -> tuple[int, bool]:
    """(число нативных строк «цена неизвестна», есть ли «дыра» записи) с момента ``since``.

    Провайдеры без цены в ответе (Outsee, ElevenLabs) пишут строку usd=0 с пометкой cost_unknown; такую строку потолки
    уже считают фиксированной оценкой. Резерв слоя при этом не должен висеть вечно поверх неё (F09-D1).
    """
    if project_id is None:
        return 0, False
    from app.services import spend_guard
    await spend_guard.ensure_schema(session)
    rows = (await session.scalars(select(spend_guard.SpendLedger).where(
        spend_guard.SpendLedger.project_id == project_id,
        spend_guard.SpendLedger.created_at >= since,
        spend_guard.SpendLedger.kind != "event",
        spend_guard.SpendLedger.source != "action_layer"))).all()
    unknown = gap = 0
    for r in rows:
        note = (r.note or "").lower()
        if "write_failed" in note:
            gap += 1
        elif "cost_unknown" in note:
            unknown += 1
    return unknown, bool(gap)


async def guard_kill_switch(session) -> None:
    """Бросает KillSwitchActive до любого шага (и чтения тоже)."""
    await kill_switch.guard(session)


# --- C-9: резерв, который не закрылся фактом --------------------------------------------------------------
# Резерв остаётся, когда факт неизвестен (таймаут без подтверждённой остановки, итог шага не подтверждён, шаг без
# проекта, строка «цена неизвестна»). Это консервативно, но без метки такой резерв висел бы вечно и молча занимал
# потолок. Здесь он получает явную метку в журнале слоя и может быть снят оператором после проверки кабинета.

_TERMINAL_STEP = (store.STEP_SUCCEEDED, store.STEP_FAILED, store.STEP_UNKNOWN, store.STEP_CANCELLED)


async def unresolved_reserves(session, *, older_than_s: float = 0.0, now: datetime | None = None,
                              skip_plans: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """Шаги, у которых резерв кредитов не закрыт фактом: шаг завершён, ``cost_actual`` пуст, сумма записей
    ``action_layer_spend`` по шагу больше нуля. Шаги из ``skip_plans`` (серверная задача ещё наблюдается) пропускаются."""
    cutoff = (now or datetime.utcnow()) - timedelta(seconds=max(0.0, float(older_than_s)))
    held = (select(store.ActionSpend.plan_id.label("plan_id"), store.ActionSpend.step_index.label("idx"),
                   func.sum(store.ActionSpend.credits).label("held"))
            .group_by(store.ActionSpend.plan_id, store.ActionSpend.step_index)
            .having(func.sum(store.ActionSpend.credits) > 1e-9)).subquery()
    q = (select(store.ActionPlanStep, held.c.held)
         .join(held, (held.c.plan_id == store.ActionPlanStep.plan_id) & (held.c.idx == store.ActionPlanStep.idx))
         .where(store.ActionPlanStep.cost_actual.is_(None), store.ActionPlanStep.status.in_(_TERMINAL_STEP)))
    out: list[dict] = []
    for step, amount in (await session.execute(q)).all():
        if step.plan_id in skip_plans:
            continue
        ended = step.finished_at or step.started_at
        if ended is None or ended > cutoff:
            continue
        out.append({"plan_id": step.plan_id, "step_index": step.idx, "action_id": step.action_id,
                    "status": step.status, "held_credits": round(float(amount), 6),
                    "ended_at": ended.isoformat() + "Z"})
    return out


async def mark_unresolved_reserves(session, rows: list[dict]) -> list[dict]:
    """Явная метка в журнале слоя (по одной на шаг, повтор не дублируется). Резерв не меняется. Возвращает новые метки."""
    fresh: list[dict] = []
    for row in rows:
        seen = await session.scalar(select(func.count()).select_from(store.ActionHistory).where(
            store.ActionHistory.event == "reserve_unresolved", store.ActionHistory.plan_id == row["plan_id"],
            store.ActionHistory.step_index == row["step_index"]))
        if seen:
            continue
        store.append_history(
            session, event="reserve_unresolved", plan_id=row["plan_id"], step_index=row["step_index"],
            actor="system", action_id=row["action_id"], outcome="held",
            error=(f"Резерв {row['held_credits']:g} кр. не закрыт фактом: итог шага ({row['status']}) неизвестен. "
                   "Сумма удерживается в потолках. Сверьте кабинет провайдера и снимите резерв вручную."),
            cost_recorded=row["held_credits"])
        fresh.append(row)
    return fresh


async def release_reserve(session, *, plan_id: str, step_index: int, operator: str, reason: str) -> dict:
    """Снять незакрытый резерв по решению оператора (он сверил кабинет провайдера). Записи только добавляются:
    отрицательная сверка в ``action_layer_spend`` и в ``spend_ledger``. Без причины и для закрытого шага - отказ."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise BudgetError("reason_required", "Укажите причину: что именно вы проверили в кабинете провайдера.")
    rows = [r for r in await unresolved_reserves(session)
            if r["plan_id"] == plan_id and r["step_index"] == int(step_index)]
    if not rows:
        raise BudgetError("not_releasable", "У этого шага нет незакрытого резерва (шаг не завершён или резерв уже снят).")
    held = float(rows[0]["held_credits"])
    from app.services import spend_guard
    from app.services.kie_catalog import CREDIT_USD
    await spend_guard.settle(session, project_id=await _plan_project(session, plan_id), run_id=f"plan:{plan_id}",
                             reserved_usd=held * CREDIT_USD, actual_usd=0.0, source="action_layer",
                             credits_delta=-held, basis="released_by_operator",
                             ref=f"plan:{plan_id}#{step_index}")
    session.add(store.ActionSpend(session_key=session_key(plan_id), plan_id=plan_id, step_index=int(step_index),
                                  kind="settle", credits=-held))
    store.append_history(session, event="reserve_released", plan_id=plan_id, step_index=int(step_index),
                         actor=operator, action_id=rows[0]["action_id"], outcome="released", error=reason[:500],
                         cost_recorded=held)
    return {"plan_id": plan_id, "step_index": int(step_index), "released_credits": held}
