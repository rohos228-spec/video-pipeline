from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from app.services import kill_switch
from app.services.action_layer import budget, store
from app.services.action_layer.context import ActionContext
from app.services.action_layer.messages_ru import validation_list_ru
from app.services.action_layer.spec import ActionRegistry, ActionSpec, registry as default_registry

_TASKS: dict[str, asyncio.Task] = {}
_CURRENT: dict[str, dict] = {}
_RESULT_LIMIT = 20000
KILL_EXEMPT_ACTIONS = frozenset({"project.stop"})
_ORPHANS: dict[int, dict] = {}


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def stop_grace_s() -> float:
    return _env_float("ACTION_LAYER_STOP_GRACE_S", 30.0)


def orphan_max_s() -> float:
    return _env_float("ACTION_LAYER_ORPHAN_MAX_S", 3600.0)


_CLAIMS: dict[int, dict] = {}
_CLAIM_TTL_S = 60.0


def claim_project(project_id: int | None, plan_id: str) -> str | None:
    if project_id is None:
        return None
    pid = int(project_id)
    cur = _CLAIMS.get(pid)
    if cur is not None and cur["plan_id"] != plan_id and time.monotonic() < cur["until"]:
        return cur["plan_id"]
    _CLAIMS[pid] = {"plan_id": plan_id, "until": time.monotonic() + _CLAIM_TTL_S}
    return None


def release_claim(plan_id: str) -> None:
    for pid in [k for k, v in _CLAIMS.items() if v["plan_id"] == plan_id]:
        _CLAIMS.pop(pid, None)


def orphan_busy(project_id: int | None) -> dict | None:
    if project_id is None:
        return None
    info = _ORPHANS.get(int(project_id))
    if info is None:
        return None
    if info.get("until") is not None and time.monotonic() >= info["until"]:
        _ORPHANS.pop(int(project_id), None)
        return None
    return info


async def orphan_busy_db(session, project_id: int | None, plan_id: str = "") -> dict | None:
    if project_id is None:
        return None
    since = _now() - timedelta(seconds=orphan_max_s())
    rows = (await session.execute(
        select(store.ActionHistory.plan_id, store.ActionHistory.action_id,
               store.ActionHistory.created_at)
        .join(store.ActionPlan, store.ActionPlan.id == store.ActionHistory.plan_id)
        .where(store.ActionPlan.project_id == int(project_id),
               store.ActionHistory.event == "job_stop_after_failure",
               store.ActionHistory.outcome == "unconfirmed",
               store.ActionHistory.created_at >= since)
        .order_by(store.ActionHistory.created_at.desc()))).all()
    for pid_, action_id, created in rows:
        if pid_ == plan_id:
            continue
        released = await session.scalar(
            select(store.ActionHistory.id).where(
                store.ActionHistory.plan_id == pid_,
                store.ActionHistory.event == "job_orphan_released",
                store.ActionHistory.created_at >= created).limit(1))
        if released is None:
            return {"plan_id": pid_, "action_id": action_id, "from_journal": True}
    return None


async def _journal_orphan_released(plan_id: str, action_id: str) -> None:
    try:
        async with store.sessions()() as s:
            store.append_history(s, event="job_orphan_released", plan_id=plan_id, action_id=action_id,
                                 outcome="released", actor="system")
            await s.commit()
    except Exception:
        logger.exception("action layer orphan release journal failed plan={}", plan_id)


class _Stopped(Exception):
    pass


class _KillSwitchStopped(Exception):
    pass


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
    if is_live(plan_id):
        return _TASKS[plan_id]
    task = asyncio.create_task(
        _run_plan(app, plan_id, operator, registry or default_registry),
        name=f"action-layer-{plan_id}")
    _TASKS[plan_id] = task
    for info in _CLAIMS.values():
        if info["plan_id"] == plan_id:
            info["until"] = float("inf")
    return task


async def wait(plan_id: str) -> None:
    task = _TASKS.get(plan_id)
    if task is not None:
        await asyncio.shield(task)


async def drain_background() -> None:
    """Отменить планы и сторожей сирот и дождаться их, пока сессия ещё жива."""
    pending: list[asyncio.Task] = []
    for task in list(_TASKS.values()):
        if not task.done():
            task.cancel()
            pending.append(task)
    for info in list(_ORPHANS.values()):
        task = info.get("task")
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
            pending.append(task)
    if pending:
        _done, still = await asyncio.wait(pending, timeout=5)
        if still:
            logger.warning(
                "action layer: drain_background timeout, осталось задач: {}",
                len(still),
            )
    _TASKS.clear()
    _ORPHANS.clear()


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
        raise
    except Exception as exc:
        logger.exception("action layer plan {} crashed", plan_id)
        await _finish(plan_id, store.PLAN_FAILED, error=f"внутренняя ошибка: {type(exc).__name__}: {exc}")
    finally:
        _TASKS.pop(plan_id, None)
        _CURRENT.pop(plan_id, None)
        release_claim(plan_id)


async def _step_update(step_id: int, **values: Any) -> None:
    async with store.sessions()() as s:
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.id == step_id).values(**values))
        await s.commit()


async def _close_step(plan_id: str, step: "store.ActionPlanStep", status: str, error: str,
                      event: str, operator: str,
                      estimate: tuple[float | None, float | None] = (None, None),
                      actual: float | None = None) -> None:
    values: dict[str, Any] = {"status": status, "error": error[:2000], "finished_at": _now()}
    if actual is not None:
        values["cost_actual"] = actual
    async with store.sessions()() as s:
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.id == step.id).values(**values))
        store.append_history(s, event=event, plan_id=plan_id, step_index=step.idx,
                             actor=operator, action_id=step.action_id, params=step.params,
                             outcome=status, error=error, cost_estimate=estimate,
                             cost_recorded=actual)
        await s.commit()


async def verify_attachments(params: dict) -> str | None:
    from app.services.action_layer import attachments as att
    path = params.get("source_path")
    if not isinstance(path, str) or not path.replace("\\", "/").startswith(att.ATTACH_DIR + "/"):
        return None
    rel = path.replace("\\", "/")
    async with store.sessions()() as s:
        row = await s.scalar(select(att.ActionAttachment).where(att.ActionAttachment.rel_path == rel))
    if row is None:
        return "вложение не найдено в реестре загрузок: подмена пути или файл не загружался"
    try:
        real = att.abs_path(rel)
    except att.AttachmentError as exc:
        return str(exc)

    def _digest() -> tuple[str, int] | None:
        if not real.is_file():
            return None
        h, size = hashlib.sha256(), 0
        with real.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
        return h.hexdigest(), size

    got = await asyncio.to_thread(_digest)
    if got is None:
        return "файл вложения не найден на диске"
    if got[0] != row.sha256 or got[1] != int(row.size):
        return "файл вложения изменился после загрузки (sha256 не совпадает): запуск отклонён"
    return None


async def _settle_step(plan_id: str, step: "store.ActionPlanStep", spec: ActionSpec,
                       reserved: float, t0: datetime, reported: float | None) -> float | None:
    if not spec.spends_money and reported is None:
        return None
    if spec.deferred_spend:
        logger.info("action layer: reserve of plan={} step={} ({}) is held, spend happens in the worker",
                    plan_id, step.idx, spec.id)
        return None
    try:
        async with store.sessions()() as s:
            actual, native = reported, False
            if actual is None:
                pid = await budget._plan_project(s, plan_id)
                actual = await budget.measure_native_credits(s, project_id=pid, since=t0)
                native = actual is not None
                if actual is None and reserved:
                    unknown, gap = await budget.native_unknown_cost_rows(s, project_id=pid, since=t0)
                    if unknown and not gap:
                        await budget.settle(s, key=budget.session_key(plan_id), plan_id=plan_id,
                                            step_index=step.idx, reserved=reserved, actual=0.0,
                                            native_counted=True)
                        await s.commit()
                        return None
            await budget.settle(s, key=budget.session_key(plan_id), plan_id=plan_id,
                                step_index=step.idx, reserved=reserved, actual=actual,
                                native_counted=native)
            await s.commit()
        return actual
    except Exception:
        logger.exception("action layer settle failed plan={} step={}", plan_id, step.idx)
        return None


async def _abort_job(plan_id: str, spec: ActionSpec, cur: dict) -> bool:
    await _call_stop(plan_id, cur)
    if cur.get("start") is None:
        return False
    deadline = time.monotonic() + stop_grace_s()
    while True:
        try:
            state = await spec.poll(cur["ctx"], cur["params"], cur["start"])
            if state.done:
                return True
        except Exception as exc:
            logger.warning("action layer poll after stop failed plan={}: {}", plan_id, exc)
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(min(float(spec.poll_interval_s), 1.0))


def _hold_project(project_id: int | None, plan_id: str, spec: ActionSpec, cur: dict,
                  on_done) -> None:
    if project_id is None:
        return
    pid = int(project_id)
    info = {"plan_id": plan_id, "action_id": spec.id,
            "until": time.monotonic() + (orphan_max_s() if cur.get("start") is not None
                                         else min(120.0, orphan_max_s()))}
    _ORPHANS[pid] = info

    async def _watch() -> None:
        try:
            while cur.get("start") is not None and time.monotonic() < info["until"]:
                await asyncio.sleep(max(0.2, min(float(spec.poll_interval_s), 5.0)))
                try:
                    state = await spec.poll(cur["ctx"], cur["params"], cur["start"])
                except Exception as exc:
                    logger.warning("orphan poll failed plan={}: {}", plan_id, exc)
                    continue
                if state.done:
                    await on_done(state)
                    await _journal_orphan_released(plan_id, spec.id)
                    return
            while time.monotonic() < info["until"]:
                await asyncio.sleep(0.5)
        finally:
            if _ORPHANS.get(pid) is info:
                _ORPHANS.pop(pid, None)

    task = asyncio.create_task(_watch(), name=f"action-layer-orphan-{plan_id}")
    info["task"] = task


async def _run_step(app: Any, plan_id: str, step_id: int, operator: str,
                    registry: ActionRegistry) -> str:
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

    if step.status == store.STEP_RUNNING:
        if spec.idempotent or spec.risk == "read":
            await _step_update(step.id, status=store.STEP_PENDING)
        else:
            await _close_step(plan_id, step, store.STEP_UNKNOWN,
                              "результат неизвестен после прерывания, нужен ручной разбор",
                              "step_unknown", operator)
            return "результат неизвестен после прерывания, нужен ручной разбор"

    if spec.id not in KILL_EXEMPT_ACTIONS and await _kill_switch_on():
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
        except Exception as exc:
            reason = f"проверка не выполнена: {type(exc).__name__}: {exc}"
        if reason:
            await _close_step(plan_id, step, store.STEP_FAILED,
                              f"условие {pre.id}: {reason}", "step_failed", operator,
                              est_pair)
            return f"не выполнено условие ({pre.id})"

    bad_file = await verify_attachments(step.params or {})
    if bad_file:
        await _close_step(plan_id, step, store.STEP_FAILED, bad_file, "step_failed",
                          operator, est_pair)
        return "вложение изменено или не найдено"

    reserved = 0.0
    key = budget.session_key(plan_id)
    if spec.spends_money:
        await mark_stale_reserves()
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
    cur = {"ctx": ctx, "spec": spec, "params": step.params, "stop_sent": False,
           "start": None, "job_finished": False}
    _CURRENT[plan_id] = cur
    from app.settings import settings
    t0 = _now()
    project_id = (await _plan_project(plan_id)) if spec.is_job else None
    try:
        result, credits = await asyncio.wait_for(
            _execute(plan_id, spec, ctx, step.params, cur),
            timeout=float(settings.action_layer_step_timeout_s))
    except asyncio.TimeoutError:
        confirmed = await _abort_after_failure(plan_id, step, spec, cur, project_id,
                                               reserved, t0, operator, est_pair, force=True)
        actual = await _settle_if_done(plan_id, step, spec, reserved, t0, confirmed)
        tail = "" if confirmed else "; остановка серверной задачи не подтверждена, проект занят до её конца"
        await _close_step(plan_id, step, store.STEP_UNKNOWN,
                          "шаг не уложился во время, результат неизвестен" + tail,
                          "step_unknown", operator, est_pair, actual)
        return "время вышло, результат неизвестен"
    except _Stopped:
        actual = await _settle_step(plan_id, step, spec, reserved, t0, None)
        await _close_step(plan_id, step, store.STEP_CANCELLED, "остановлено оператором",
                          "step_cancelled", operator, est_pair, actual)
        return "stopped"
    except _KillSwitchStopped:
        confirmed = await _abort_after_failure(plan_id, step, spec, cur, project_id,
                                               reserved, t0, operator, est_pair, force=False)
        actual = await _settle_if_done(plan_id, step, spec, reserved, t0, confirmed)
        await _close_step(plan_id, step, store.STEP_CANCELLED,
                          "остановлено аварийным стопом", "step_cancelled", operator, est_pair,
                          actual)
        return "blocked"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        confirmed = True
        if spec.is_job and cur.get("start") is not None and not cur.get("job_finished"):
            confirmed = await _abort_after_failure(plan_id, step, spec, cur, project_id,
                                                   reserved, t0, operator, est_pair, force=False)
        actual = await _settle_if_done(plan_id, step, spec, reserved, t0, confirmed)
        await _close_step(plan_id, step, store.STEP_FAILED,
                          f"{type(exc).__name__}: {exc}", "step_failed", operator, est_pair,
                          actual)
        return f"{type(exc).__name__}: {exc}"[:300]
    finally:
        _CURRENT.pop(plan_id, None)

    actual = await _settle_step(plan_id, step, spec, reserved, t0, credits)
    async with store.sessions()() as s:
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.id == step.id).values(
            status=store.STEP_SUCCEEDED, result=safe_json(result), progress=1.0,
            cost_actual=actual, finished_at=_now(), error=""))
        store.append_history(s, event="step_succeeded", plan_id=plan_id, step_index=step.idx,
                             actor=operator, action_id=step.action_id, params=step.params,
                             outcome="succeeded", cost_estimate=est_pair, cost_recorded=actual)
        await s.commit()
    return "ok"


async def mark_stale_reserves() -> list[dict]:
    from app.settings import settings
    try:
        async with store.sessions()() as s:
            rows = await budget.unresolved_reserves(
                s, older_than_s=float(settings.action_layer_reserve_stale_s),
                skip_plans={info["plan_id"] for info in _ORPHANS.values()})
            fresh = await budget.mark_unresolved_reserves(s, rows)
            await s.commit()
        return fresh
    except Exception:
        logger.exception("action layer stale reserve sweep failed")
        return []


async def _plan_project(plan_id: str) -> int | None:
    async with store.sessions()() as s:
        return await budget._plan_project(s, plan_id)


async def _settle_if_done(plan_id, step, spec, reserved, t0, confirmed: bool) -> float | None:
    if not confirmed:
        return None
    return await _settle_step(plan_id, step, spec, reserved, t0, None)


async def _abort_after_failure(plan_id, step, spec, cur, project_id, reserved, t0, operator,
                               est_pair, *, force: bool) -> bool:
    if not spec.is_job:
        return True
    if not (force or cur.get("start") is not None):
        return True
    try:
        confirmed = await _abort_job(plan_id, spec, cur)
    except Exception:
        logger.exception("action layer abort failed plan={}", plan_id)
        confirmed = False
    async with store.sessions()() as s:
        store.append_history(s, event="job_stop_after_failure", plan_id=plan_id,
                             step_index=step.idx, actor=operator, action_id=spec.id,
                             outcome="confirmed" if confirmed else "unconfirmed",
                             error="" if confirmed else "серверная задача могла продолжиться",
                             cost_estimate=est_pair)
        await s.commit()
    if not confirmed:
        async def _late_settle(_state) -> None:
            await _settle_step(plan_id, step, spec, reserved, t0, None)
        _hold_project(project_id, plan_id, spec, cur, _late_settle)
    return confirmed


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
    except Exception as exc:
        logger.warning("action layer stop call failed plan={}: {}", plan_id, exc)


async def _execute(plan_id: str, spec: ActionSpec, ctx: ActionContext, params: dict,
                   cur: dict) -> tuple[dict, float | None]:
    start = await spec.executor(ctx, params)
    start = start if isinstance(start, dict) else {"value": start}
    cur["start"] = start
    if not spec.is_job:
        return start, _credits(start.get("credits_spent"))
    while True:
        state = await spec.poll(ctx, params, start)
        if state.progress is not None or state.note:
            await ctx.progress(state.progress, state.note)
        if state.done:
            cur["job_finished"] = True
            if state.ok:
                return {**start, **(state.result or {})}, _credits(state.credits_spent)
            if await _stop_requested(plan_id):
                raise _Stopped()
            raise RuntimeError(state.error or "фоновая задача завершилась ошибкой")
        if await _stop_requested(plan_id):
            await _call_stop(plan_id, cur)
        if await _kill_switch_on():
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
        await _finish(plan_id, store.PLAN_STOPPED, reason="operator_stop")
        return {"status": store.PLAN_STOPPED, "stop_requested": True}
    return {"status": store.PLAN_RUNNING, "stop_requested": True}
