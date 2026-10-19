from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.services.action_layer import approval, budget, executor, store
from app.services.action_layer.context import ActionContext
from app.services.action_layer.plan import Plan, build_preview, plan_hash
from app.services.action_layer.spec import ActionRegistry, ensure_builtin_actions
from app.services.action_layer.spec import registry as default_registry


class RunRefused(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _max_steps() -> int:
    from app.settings import settings
    return int(settings.action_layer_max_steps)


def _plan_from_rows(row: "store.ActionPlan", steps: list["store.ActionPlanStep"]) -> Plan:
    return Plan([store_step_to_plan(s) for s in steps], row.request_text)


def store_step_to_plan(s: "store.ActionPlanStep"):
    from app.services.action_layer.plan import PlanStep
    return PlanStep(s.idx, s.action_id, dict(s.params or {}), list(s.depends_on or []))


async def create_plan(app: Any, steps_json: Any, *, operator: approval.OperatorIdentity,
                      request_text: str = "",
                      registry: ActionRegistry | None = None) -> tuple[str | None, dict]:
    registry = registry or default_registry
    ensure_builtin_actions()
    plan = Plan.from_json(steps_json, max_steps=_max_steps(), request_text=request_text)
    ctx = ActionContext(app=app, operator=operator.id, read_only=True)
    preview = await build_preview(plan, registry, ctx)
    preview["budget"] = budget.preview_budget(preview["cost"])
    if not preview["ok"]:
        return None, preview
    row = store.ActionPlan(
        project_id=plan.project_id(), operator=operator.id, request_text=request_text[:2000],
        plan_json={"steps": plan.steps_json()}, plan_hash=preview["plan_hash"],
        risk_max=preview["risk_max"], needs_approval=preview["needs_approval"],
        cost_low=preview["cost"]["low"], cost_high=preview["cost"]["high"],
        cost_unknown_steps=preview["cost"]["unknown_steps"])
    async with store.sessions()() as s:
        s.add(row)
        await s.flush()
        for step in plan.steps:
            s.add(store.ActionPlanStep(
                plan_id=row.id, idx=step.index, action_id=step.action_id, params=step.params,
                depends_on=step.depends_on, idempotency_key=f"{row.id}:{step.index}"))
        store.append_history(s, event="plan_created", plan_id=row.id, actor=operator.id,
                             request_text=request_text, outcome="proposed",
                             params={"steps": plan.steps_json()},
                             cost_estimate=(preview["cost"]["low"], preview["cost"]["high"]))
        await s.commit()
    preview["plan_id"] = row.id
    return row.id, preview


async def _load(s, plan_id: str) -> tuple["store.ActionPlan", list["store.ActionPlanStep"]]:
    row = await s.get(store.ActionPlan, plan_id)
    if row is None:
        raise LookupError("План не найден.")
    steps = (await s.scalars(select(store.ActionPlanStep).where(
        store.ActionPlanStep.plan_id == plan_id).order_by(store.ActionPlanStep.idx))).all()
    return row, list(steps)


async def approve_plan(app: Any, plan_id: str, operator: approval.OperatorIdentity, *,
                       registry: ActionRegistry | None = None,
                       ttl_s: int | None = None) -> dict:
    registry = registry or default_registry
    ensure_builtin_actions()
    from app.services.action_layer import planner
    await planner.check_plan_fresh(plan_id)
    async with store.sessions()() as s:
        row, steps = await _load(s, plan_id)
        plan = _plan_from_rows(row, steps)
        if plan_hash(plan, registry) != row.plan_hash:
            raise approval.ApprovalError("plan_changed", "План или определения действий изменились после предпросмотра. Составьте план заново.")
        preview = await build_preview(
            plan, registry, ActionContext(app=app, operator=operator.id, read_only=True))
        if not preview["ok"]:
            raise approval.ApprovalError("plan_invalid", "План не прошёл проверку: " + "; ".join(preview["errors"][:5]))
        if row.needs_approval:
            spent = await budget.spent(s, budget.session_key(plan_id))
            fit = budget.preview_budget(preview["cost"], already_spent=spent)
            if not fit["fits"]:
                raise approval.ApprovalError(
                    "over_budget", "Стоимость неизвестна или не помещается в потолок расходов сессии.")
        appr = await approval.create_approval(s, row, operator, ttl_s=ttl_s)
        await s.commit()
        return {"approval_id": appr.id, "plan_id": plan_id, "plan_hash": appr.plan_hash,
                "operator": appr.operator, "expires_at": appr.expires_at.isoformat() + "Z"}


_PREPARE_LOCKS: dict[int, asyncio.Lock] = {}


def _prepare_lock() -> asyncio.Lock:
    key = id(asyncio.get_running_loop())
    lock = _PREPARE_LOCKS.get(key)
    if lock is None:
        _PREPARE_LOCKS.clear()
        lock = _PREPARE_LOCKS[key] = asyncio.Lock()
    return lock


async def prepare_run(plan_id: str, operator: approval.OperatorIdentity, *,
                      registry: ActionRegistry | None = None) -> "store.ActionPlan":
    async with _prepare_lock():
        try:
            return await _prepare_run(plan_id, operator, registry=registry)
        except BaseException:
            executor.release_claim(plan_id)
            raise


async def _prepare_run(plan_id: str, operator: approval.OperatorIdentity, *,
                       registry: ActionRegistry | None = None) -> "store.ActionPlan":
    registry = registry or default_registry
    ensure_builtin_actions()
    from app.services.action_layer import planner
    await planner.check_plan_fresh(plan_id)
    async with store.sessions()() as s:
        row, steps = await _load(s, plan_id)
        if executor.is_live(plan_id):
            raise RunRefused("already_running", "План уже выполняется.")
        if row.status in (store.PLAN_SUCCEEDED, store.PLAN_FAILED):
            raise RunRefused("plan_finished", f"План уже завершён (состояние «{row.status}»): составьте новый.")
        if plan_hash(_plan_from_rows(row, steps), registry) != row.plan_hash:
            raise RunRefused("plan_changed", "План или определения действий изменились после предпросмотра. Составьте план заново.")
        if await executor._kill_switch_on() and not all(
                st.action_id in executor.KILL_EXEMPT_ACTIONS for st in steps):
            raise RunRefused("kill_switch_active", "Аварийная остановка включена оператором: планы не запускаются.")
        for st in steps:
            if st.status != store.STEP_SUCCEEDED:
                bad = await executor.verify_attachments(st.params or {})
                if bad:
                    raise RunRefused("attachment_changed", bad)
        if row.project_id is not None:
            orphan = executor.orphan_busy(row.project_id)
            if orphan is not None and orphan["plan_id"] != plan_id:
                raise RunRefused(
                    "project_busy",
                    "Серверная платная задача предыдущего плана ещё не подтверждённо остановлена: "
                    "дождитесь её конца или остановите проект.")
            orphan_db = await executor.orphan_busy_db(s, row.project_id, plan_id)
            if orphan_db is not None:
                raise RunRefused(
                    "project_busy",
                    "Серверная платная задача предыдущего плана не была подтверждённо остановлена "
                    "(запись в журнале, сервер мог перезапуститься): дождитесь окна наблюдения "
                    "или остановите проект.")
            holder = executor.claim_project(row.project_id, plan_id)
            if holder is not None:
                raise RunRefused("project_busy", "В этом проекте уже запускается другой план.")
            others = (await s.scalars(select(store.ActionPlan.id).where(
                store.ActionPlan.project_id == row.project_id,
                store.ActionPlan.status == store.PLAN_RUNNING,
                store.ActionPlan.id != plan_id))).all()
            if any(executor.is_live(pid) for pid in others):
                raise RunRefused("project_busy", "В этом проекте уже выполняется другой план.")
        interrupted = row.status == store.PLAN_RUNNING
        if row.needs_approval:
            if interrupted:
                if not row.approval_id:
                    raise approval.ApprovalError("approval_required", "Запуск не был подтверждён оператором.")
                if _resume_expired(row, steps):
                    row.status = store.PLAN_STOPPED
                    row.stop_reason = "resume_window_expired"
                    row.updated_at = row.finished_at = datetime.utcnow()
                    store.append_history(s, event="resume_refused", plan_id=plan_id,
                                         actor=operator.id, outcome="expired",
                                         error="окно возобновления истекло")
                    await s.commit()
                    raise approval.ApprovalError(
                        "approval_required",
                        "Окно возобновления истекло: нужно новое подтверждение оператора")
            else:
                appr = await approval.consume_approval(s, row)
                row.approval_id = appr.id
        if row.status == store.PLAN_STOPPED:
            for st in steps:
                if st.status in (store.STEP_SKIPPED, store.STEP_CANCELLED):
                    st.status = store.STEP_PENDING
                    st.error = ""
        row.status = store.PLAN_RUNNING
        row.stop_requested = False
        row.stop_reason = ""
        row.error = ""
        row.finished_at = None
        row.updated_at = datetime.utcnow()
        store.append_history(s, event="run_requested", plan_id=plan_id, actor=operator.id,
                             outcome="resume" if interrupted else "start")
        await s.commit()
        return row


def _resume_window_s() -> int:
    from app.settings import settings
    return int(settings.action_layer_resume_window_s)


def _last_activity(row, steps) -> datetime | None:
    stamps = [row.updated_at, row.created_at]
    for st in steps:
        stamps += [st.started_at, st.finished_at]
    stamps = [t for t in stamps if t is not None]
    return max(stamps) if stamps else None


def _resume_expired(row, steps, now: datetime | None = None) -> bool:
    last = _last_activity(row, steps)
    if last is None:
        return True
    return ((now or datetime.utcnow()) - last).total_seconds() > _resume_window_s()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


async def plan_view(plan_id: str) -> dict:
    async with store.sessions()() as s:
        row, steps = await _load(s, plan_id)
        approvals = (await s.scalars(select(store.ActionApproval).where(
            store.ActionApproval.plan_id == plan_id).order_by(
            store.ActionApproval.created_at.desc()))).all()
        spent = await budget.spent(s, budget.session_key(plan_id))
    now = datetime.utcnow()
    actuals = [st.cost_actual for st in steps if st.cost_actual is not None]
    live = executor.is_live(plan_id)
    return {
        "id": row.id, "status": row.status, "project_id": row.project_id,
        "operator": row.operator, "request_text": row.request_text,
        "plan_hash": row.plan_hash, "risk_max": row.risk_max,
        "needs_approval": row.needs_approval,
        "cost_estimate": {"low": row.cost_low, "high": row.cost_high,
                          "unknown_steps": row.cost_unknown_steps},
        "spent_credits": spent, "budget_cap_credits": budget.session_cap(),
        "reserved_credits": spent, "actual_credits": sum(actuals) if actuals else None,
        "resume_window_s": _resume_window_s(),
        "resume_expired": (row.status == store.PLAN_RUNNING and not live
                           and _resume_expired(row, steps, now)),
        "stop_requested": row.stop_requested, "stop_reason": row.stop_reason,
        "error": row.error, "live": live,
        "interrupted": row.status == store.PLAN_RUNNING and not live,
        "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
        "finished_at": _iso(row.finished_at),
        "approvals": [{"id": a.id, "operator": a.operator, "created_at": _iso(a.created_at),
                       "expires_at": _iso(a.expires_at), "used_at": _iso(a.used_at),
                       "active": a.used_at is None and a.expires_at > now}
                      for a in approvals],
        "steps": [{"index": st.idx, "action_id": st.action_id, "params": st.params,
                   "depends_on": st.depends_on, "status": st.status, "attempts": st.attempts,
                   "progress": st.progress, "progress_note": st.progress_note,
                   "result": st.result, "error": st.error,
                   "cost": {"low": st.cost_low, "high": st.cost_high, "actual": st.cost_actual},
                   "started_at": _iso(st.started_at), "finished_at": _iso(st.finished_at)}
                  for st in steps],
    }


async def list_plans(*, status: str | None = None, project_id: int | None = None,
                     limit: int = 20) -> dict:
    limit = max(1, min(int(limit), 100))
    async with store.sessions()() as s:
        q = select(store.ActionPlan).order_by(store.ActionPlan.created_at.desc()).limit(limit)
        if status:
            q = q.where(store.ActionPlan.status == status)
        if project_id is not None:
            q = q.where(store.ActionPlan.project_id == project_id)
        rows = (await s.scalars(q)).all()
    return {"items": [{
        "id": r.id, "status": r.status, "project_id": r.project_id,
        "request_text": r.request_text, "live": executor.is_live(r.id),
        "interrupted": r.status == store.PLAN_RUNNING and not executor.is_live(r.id),
        "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at)} for r in rows]}


async def history(*, plan_id: str | None = None, limit: int = 100,
                  before_id: int | None = None) -> dict:
    limit = max(1, min(int(limit), 500))
    async with store.sessions()() as s:
        q = select(store.ActionHistory).order_by(store.ActionHistory.id.desc()).limit(limit)
        if plan_id:
            q = q.where(store.ActionHistory.plan_id == plan_id)
        if before_id:
            q = q.where(store.ActionHistory.id < before_id)
        rows = (await s.scalars(q)).all()
        total = await s.scalar(select(func.count(store.ActionHistory.id)))
    return {"total": int(total or 0), "items": [{
        "id": h.id, "created_at": _iso(h.created_at), "plan_id": h.plan_id,
        "step_index": h.step_index, "event": h.event, "actor": h.actor,
        "request_text": h.request_text, "action_id": h.action_id, "params": h.params,
        "outcome": h.outcome, "error": h.error,
        "cost_estimate": {"low": h.cost_estimate_low, "high": h.cost_estimate_high},
        "cost_recorded": h.cost_recorded} for h in rows]}
