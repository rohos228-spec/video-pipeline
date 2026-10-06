"""Фоновое исполнение плана: шаг за шагом, с остановкой и возобновлением.

Исполнители вызывают существующий backend внутри процесса через
``ActionContext.call`` (ASGI без сети), поэтому валидация эндпоинтов, сессии
проектов и события сохраняются. Состояние (статусы шагов, прогресс, резерв)
лежит в БД, поэтому после рестарта план можно продолжить: завершённые шаги
пропускаются, шаг с неизвестным исходом платного/непереключаемого действия
повторно не запускается.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from app.services import kill_switch
from app.services.action_layer import budget, store
from app.services.action_layer.context import ActionContext
from app.services.action_layer.messages_ru import validation_list_ru
from app.services.action_layer.spec import ActionRegistry, ActionSpec, registry as default_registry

_TASKS: dict[str, asyncio.Task] = {}
# Текущий шаг плана: нужен, чтобы stop мог вызвать stop-эндпоинт действия.
_CURRENT: dict[str, dict] = {}
_RESULT_LIMIT = 20000


class _Stopped(Exception):
    pass


class _KillSwitchStopped(Exception):
    """Глобальный аварийный стоп включили во время выполнения фонового шага."""


def is_live(plan_id: str) -> bool:
    task = _TASKS.get(plan_id)
    return task is not None and not task.done()


def live_plan_ids() -> list[str]:
    return [pid for pid in list(_TASKS) if is_live(pid)]


def safe_json(obj: Any, limit: int = _RESULT_LIMIT) -> Any:
    try:
        raw = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"unserializable": str(obj)[:500]}
    if len(raw) > limit:
        return {"truncated": True, "preview": raw[:2000]}
    return json.loads(raw)


def _now() -> datetime:
    return datetime.utcnow()


def launch(app: Any, plan_id: str, operator: str, *,
           registry: ActionRegistry | None = None) -> asyncio.Task:
    """Стартует фоновую задачу плана (план уже переведён в running)."""
    if is_live(plan_id):
        return _TASKS[plan_id]
    task = asyncio.create_task(
        _run_plan(app, plan_id, operator, registry or default_registry),
        name=f"action-layer-{plan_id}")
    _TASKS[plan_id] = task
    return task


async def wait(plan_id: str) -> None:
    """Дождаться завершения задачи плана (для тестов и скриптов)."""
    task = _TASKS.get(plan_id)
    if task is not None:
        await asyncio.shield(task)


async def _finish(plan_id: str, status: str, *, error: str = "", reason: str = "") -> None:
    async with store.sessions()() as s:
        plan = await s.get(store.ActionPlan, plan_id)
        if plan is None:
            return
        plan.status = status
        plan.error = error[:2000]
        plan.stop_reason = reason
        plan.updated_at = plan.finished_at = _now()
        if status in (store.PLAN_FAILED, store.PLAN_STOPPED):
            await s.execute(update(store.ActionPlanStep).where(
                store.ActionPlanStep.plan_id == plan_id,
                store.ActionPlanStep.status == store.STEP_PENDING,
            ).values(status=store.STEP_SKIPPED))
        store.append_history(s, event="plan_finished", plan_id=plan_id, actor=plan.operator,
                             outcome=status, error=error or reason)
        await s.commit()


async def _stop_requested(plan_id: str) -> bool:
    async with store.sessions()() as s:
        return bool(await s.scalar(select(store.ActionPlan.stop_requested).where(
            store.ActionPlan.id == plan_id)))


async def _kill_switch_on() -> bool:
    # Отдельная сессия: is_active делает rollback при отсутствии таблицы.
    async with store.sessions()() as ks:
        return await kill_switch.is_active(ks)


async def _run_plan(app: Any, plan_id: str, operator: str, registry: ActionRegistry) -> None:
    try:
        async with store.sessions()() as s:
            steps = (await s.scalars(select(store.ActionPlanStep).where(
                store.ActionPlanStep.plan_id == plan_id).order_by(
                store.ActionPlanStep.idx))).all()
            plan = await s.get(store.ActionPlan, plan_id)
            if plan is None:
                return
            store.append_history(s, event="plan_started", plan_id=plan_id, actor=operator,
                                 request_text=plan.request_text, outcome="running")
            await s.commit()
        for step in steps:
            if await _stop_requested(plan_id):
                await _finish(plan_id, store.PLAN_STOPPED, reason="operator_stop")
                return
            outcome = await _run_step(app, plan_id, step.id, operator, registry)
            if outcome == "ok":
                continue
            if outcome == "blocked":
                await _finish(plan_id, store.PLAN_STOPPED, reason=budget.KILL_SWITCH_REASON,
                              error="аварийный стоп включён оператором")
            elif outcome == "stopped":
                await _finish(plan_id, store.PLAN_STOPPED, reason="operator_stop")
            else:
                await _finish(plan_id, store.PLAN_FAILED, error=f"шаг {step.idx + 1}: {outcome}")
            return
        await _finish(plan_id, store.PLAN_SUCCEEDED)
    except asyncio.CancelledError:
        # Процесс останавливается: статус running остаётся, план можно продолжить.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("action layer plan {} crashed", plan_id)
        await _finish(plan_id, store.PLAN_FAILED, error=f"внутренняя ошибка: {type(exc).__name__}: {exc}")
    finally:
        _TASKS.pop(plan_id, None)
        _CURRENT.pop(plan_id, None)


async def _step_update(step_id: int, **values: Any) -> None:
    async with store.sessions()() as s:
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.id == step_id).values(**values))
        await s.commit()


async def _close_step(plan_id: str, step: "store.ActionPlanStep", status: str, error: str,
                      event: str, operator: str,
                      estimate: tuple[float | None, float | None] = (None, None)) -> None:
    async with store.sessions()() as s:
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.id == step.id).values(
            status=status, error=error[:2000], finished_at=_now()))
        store.append_history(s, event=event, plan_id=plan_id, step_index=step.idx,
                             actor=operator, action_id=step.action_id, params=step.params,
                             outcome=status, error=error, cost_estimate=estimate)
        await s.commit()


async def _run_step(app: Any, plan_id: str, step_id: int, operator: str,
                    registry: ActionRegistry) -> str:
    """Возвращает 'ok' | 'blocked' | 'stopped' | текст причины провала."""
    async with store.sessions()() as s:
        step = await s.get(store.ActionPlanStep, step_id)
        plan = await s.get(store.ActionPlan, plan_id)
        statuses = {r.idx: r.status for r in (await s.scalars(select(store.ActionPlanStep).where(
            store.ActionPlanStep.plan_id == plan_id))).all()}
        request_text = plan.request_text if plan else ""
    if step.status == store.STEP_SUCCEEDED:
        return "ok"
    spec = registry.find(step.action_id)
    if spec is None:
        await _close_step(plan_id, step, store.STEP_FAILED, "неизвестное действие",
                          "step_failed", operator)
        return "неизвестное действие"

    # Шаг, начатый до рестарта: исход неизвестен.
    if step.status == store.STEP_RUNNING:
        if spec.idempotent or spec.risk == "read":
            await _step_update(step.id, status=store.STEP_PENDING)
        else:
            await _close_step(plan_id, step, store.STEP_UNKNOWN,
                              "результат неизвестен после прерывания, нужен ручной разбор",
                              "step_unknown", operator)
            return "результат неизвестен после прерывания, нужен ручной разбор"

    if await _kill_switch_on():
        async with store.sessions()() as s:
            store.append_history(s, event="kill_switch_block", plan_id=plan_id,
                                 step_index=step.idx, actor=operator, action_id=step.action_id,
                                 outcome="blocked", error=budget.KILL_SWITCH_REASON)
            await s.commit()
        return "blocked"

    for dep in step.depends_on or []:
        if statuses.get(dep) != store.STEP_SUCCEEDED:
            await _close_step(plan_id, step, store.STEP_SKIPPED,
                              f"шаг {dep + 1}, от которого зависит этот, не выполнен", "step_skipped", operator)
            return f"не выполнен шаг {dep + 1}, от которого зависит этот"

    errors = registry.validate_params(spec.id, step.params)
    est = spec.estimate(step.params)
    est_pair = (est.low, est.high)
    ctx = ActionContext(app=app, operator=operator, plan_id=plan_id, step_index=step.idx,
                        idempotency_key=step.idempotency_key)
    if errors:
        await _close_step(plan_id, step, store.STEP_FAILED, "; ".join(validation_list_ru(errors)),
                          "step_failed", operator, est_pair)
        return "неверные параметры"
    for pre in spec.preconditions:
        try:
            reason = await pre.check(ctx.readonly_copy(), step.params)
        except Exception as exc:  # noqa: BLE001 - неизвестное = не выполнено
            reason = f"проверка не выполнена: {type(exc).__name__}: {exc}"
        if reason:
            await _close_step(plan_id, step, store.STEP_FAILED,
                              f"условие {pre.id}: {reason}", "step_failed", operator,
                              est_pair)
            return f"не выполнено условие ({pre.id})"

    reserved = 0.0
    key = budget.session_key(plan_id)
    async with store.sessions()() as s:
        try:
            if spec.spends_money:
                reserved = await budget.reserve(s, key=key, plan_id=plan_id,
                                                step_index=step.idx, estimate=est)
        except budget.BudgetError as exc:
            await s.rollback()
            store.append_history(s, event="budget_block", plan_id=plan_id, step_index=step.idx,
                                 actor=operator, action_id=step.action_id, params=step.params,
                                 outcome="blocked", error=str(exc), cost_estimate=est_pair)
            await s.execute(update(store.ActionPlanStep).where(
                store.ActionPlanStep.id == step.id).values(
                status=store.STEP_FAILED, error=str(exc)[:2000], finished_at=_now()))
            await s.commit()
            return f"ограничение бюджета ({exc.code})"
        claimed = await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.id == step.id,
            store.ActionPlanStep.status == store.STEP_PENDING).values(
            status=store.STEP_RUNNING, attempts=store.ActionPlanStep.attempts + 1,
            started_at=_now(), cost_low=est.low, cost_high=est.high))
        if claimed.rowcount != 1:
            await s.rollback()
            return "шаг уже взят в работу в другом месте"
        store.append_history(s, event="step_started", plan_id=plan_id, step_index=step.idx,
                             actor=operator, request_text=request_text, action_id=step.action_id,
                             params=step.params, outcome="running", cost_estimate=est_pair)
        await s.commit()

    async def sink(fraction: float | None, note: str) -> None:
        await _step_update(step.id, progress=fraction, progress_note=note[:500])

    ctx.progress_sink = sink
    cur = {"ctx": ctx, "spec": spec, "params": step.params, "stop_sent": False}
    _CURRENT[plan_id] = cur
    from app.settings import settings
    try:
        result, credits = await asyncio.wait_for(
            _execute(plan_id, spec, ctx, step.params, cur),
            timeout=float(settings.action_layer_step_timeout_s))
    except asyncio.TimeoutError:
        await _close_step(plan_id, step, store.STEP_UNKNOWN, "шаг не уложился во время, результат неизвестен",
                          "step_unknown", operator, est_pair)
        return "время вышло, результат неизвестен"
    except _Stopped:
        await _close_step(plan_id, step, store.STEP_CANCELLED, "остановлено оператором",
                          "step_cancelled", operator, est_pair)
        return "stopped"
    except _KillSwitchStopped:
        await _close_step(plan_id, step, store.STEP_CANCELLED,
                          "остановлено аварийным стопом", "step_cancelled", operator, est_pair)
        return "blocked"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _close_step(plan_id, step, store.STEP_FAILED,
                          f"{type(exc).__name__}: {exc}", "step_failed", operator, est_pair)
        return f"{type(exc).__name__}: {exc}"[:300]
    finally:
        _CURRENT.pop(plan_id, None)

    async with store.sessions()() as s:
        await budget.settle(s, key=key, plan_id=plan_id, step_index=step.idx,
                            reserved=reserved, actual=credits)
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.id == step.id).values(
            status=store.STEP_SUCCEEDED, result=safe_json(result), progress=1.0,
            cost_actual=credits, finished_at=_now(), error=""))
        store.append_history(s, event="step_succeeded", plan_id=plan_id, step_index=step.idx,
                             actor=operator, action_id=step.action_id, params=step.params,
                             outcome="succeeded", cost_estimate=est_pair, cost_recorded=credits)
        await s.commit()
    return "ok"


def _credits(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


async def _call_stop(plan_id: str, cur: dict) -> None:
    if cur["stop_sent"] or cur["spec"].stop is None:
        return
    cur["stop_sent"] = True
    try:
        await cur["spec"].stop(cur["ctx"], cur["params"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("action layer stop call failed plan={}: {}", plan_id, exc)


async def _execute(plan_id: str, spec: ActionSpec, ctx: ActionContext, params: dict,
                   cur: dict) -> tuple[dict, float | None]:
    start = await spec.executor(ctx, params)
    start = start if isinstance(start, dict) else {"value": start}
    if not spec.is_job:
        return start, _credits(start.get("credits_spent"))
    while True:
        state = await spec.poll(ctx, params, start)
        if state.progress is not None or state.note:
            await ctx.progress(state.progress, state.note)
        if state.done:
            if state.ok:
                return {**start, **(state.result or {})}, _credits(state.credits_spent)
            if await _stop_requested(plan_id):
                raise _Stopped()
            raise RuntimeError(state.error or "фоновая задача завершилась ошибкой")
        if await _stop_requested(plan_id):
            await _call_stop(plan_id, cur)
        if await _kill_switch_on():
            # Аварийный стоп действует и на уже идущую задачу: останавливаем её и закрываем шаг.
            await _call_stop(plan_id, cur)
            async with store.sessions()() as s:
                store.append_history(s, event="kill_switch_block", plan_id=plan_id,
                                     step_index=ctx.step_index, actor=ctx.operator,
                                     action_id=spec.id, outcome="blocked",
                                     error=budget.KILL_SWITCH_REASON)
                await s.commit()
            raise _KillSwitchStopped()
        await asyncio.sleep(spec.poll_interval_s)


async def request_stop(plan_id: str, operator: str) -> dict:
    """Помечает остановку; вызывает stop-эндпоинт текущего шага, если он есть."""
    async with store.sessions()() as s:
        plan = await s.get(store.ActionPlan, plan_id)
        if plan is None:
            raise LookupError("План не найден.")
        if plan.status in (store.PLAN_SUCCEEDED, store.PLAN_FAILED, store.PLAN_STOPPED):
            return {"status": plan.status, "stop_requested": plan.stop_requested}
        plan.stop_requested = True
        plan.updated_at = _now()
        store.append_history(s, event="stop_requested", plan_id=plan_id, actor=operator,
                             outcome="requested")
        await s.commit()
    cur = _CURRENT.get(plan_id)
    if cur is not None:
        await _call_stop(plan_id, cur)
    if not is_live(plan_id):
        # Нет живой задачи (план не стартовал или прерван рестартом): закрываем сразу.
        await _finish(plan_id, store.PLAN_STOPPED, reason="operator_stop")
        return {"status": store.PLAN_STOPPED, "stop_requested": True}
    return {"status": store.PLAN_RUNNING, "stop_requested": True}
