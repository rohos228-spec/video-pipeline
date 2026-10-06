"""Исполнитель, бюджет, kill switch, журнал, остановка и возобновление."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.services import kill_switch
from app.services.action_layer import approval, budget, executor, service, store
from app.services.action_layer.spec import CostEstimate, JobState
from tests.action_layer_support import env, fresh_registry, make_spec  # noqa: F401

OP = approval.OperatorIdentity("web_operator:abc123")


async def _plan(env, reg, *steps, text="запрос"):
    plan_id, preview = await service.create_plan(env.app, list(steps), operator=OP,
                                                 request_text=text, registry=reg)
    assert plan_id, preview
    return plan_id


async def _run(env, reg, plan_id, *, approve=False):
    if approve:
        await service.approve_plan(env.app, plan_id, OP, registry=reg)
    await service.prepare_run(plan_id, OP, registry=reg)
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    await executor.wait(plan_id)
    return await service.plan_view(plan_id)


def _statuses(view):
    return [s["status"] for s in view["steps"]]


# --- бюджет ----------------------------------------------------------------

async def test_budget_cap_stops_before_the_step_that_would_exceed(env, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 25.0)
    calls: list = []

    async def paid(ctx, params):
        calls.append(params)
        return {"credits_spent": 9.5}

    reg = fresh_registry(make_spec("t.paid", paid, risk="paid", spends=True, cost=(5, 10)))
    pid = await _plan(env, reg, {"action_id": "t.paid"}, {"action_id": "t.paid", "params": {"n": 1}})
    await service.approve_plan(env.app, pid, OP, registry=reg)  # 20 <= 25
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 15.0)  # потолок снизили
    view = await _run(env, reg, pid)
    assert view["status"] == "failed"
    assert _statuses(view) == ["succeeded", "failed"]
    assert len(calls) == 1
    assert "budget_exceeded" in view["error"] or "budget" in view["error"]
    assert view["spent_credits"] == pytest.approx(9.5)  # резерв 10 сверен с фактом 9.5
    assert view["steps"][0]["cost"]["actual"] == 9.5
    hist = await service.history(plan_id=pid)
    assert "budget_block" in [h["event"] for h in hist["items"]]


async def test_reserve_is_atomic_under_concurrency(env) -> None:
    async def one():
        async with env.factory() as s:
            try:
                await budget.reserve(s, key="plan:x", plan_id="x", step_index=0,
                                     estimate=CostEstimate(10, 10, "t"), cap=25.0)
                await s.commit()
                return True
            except budget.BudgetError:
                await s.rollback()
                return False

    results = await asyncio.gather(*[one() for _ in range(5)])
    assert sum(results) == 2
    async with env.factory() as s:
        assert await budget.spent(s, "plan:x") == 20.0


async def test_unknown_estimate_is_refused(env) -> None:
    async with env.factory() as s:
        with pytest.raises(budget.BudgetError) as e:
            await budget.reserve(s, key="plan:y", plan_id="y", step_index=0,
                                 estimate=CostEstimate.unknown(), cap=1e9)
        assert e.value.code == "estimate_unknown"


# --- kill switch -----------------------------------------------------------

async def test_kill_switch_refuses_run_and_blocks_between_steps(env) -> None:
    ran: list = []

    async def first(ctx, params):
        ran.append("first")
        await kill_switch.set_state(env.factory, active=True, actor="op", rationale="stop all now")
        return {}

    async def second(ctx, params):
        ran.append("second")
        return {}

    reg = fresh_registry(make_spec("t.first", first, risk="local_safe"),
                         make_spec("t.second", second))  # даже read-шаг блокируется
    pid = await _plan(env, reg, {"action_id": "t.first"}, {"action_id": "t.second"})
    view = await _run(env, reg, pid)
    assert ran == ["first"]
    assert view["status"] == "stopped" and view["stop_reason"] == kill_switch.REASON
    assert _statuses(view) == ["succeeded", "skipped"]
    events = [h["event"] for h in (await service.history(plan_id=pid))["items"]]
    assert "kill_switch_block" in events

    # Пока переключатель включён, новый запуск отклоняется сразу.
    pid2 = await _plan(env, reg, {"action_id": "t.second"})
    with pytest.raises(service.RunRefused) as e:
        await service.prepare_run(pid2, OP, registry=reg)
    assert e.value.code == "kill_switch_active"
    await kill_switch.set_state(env.factory, active=False, actor="op", rationale="resume ok")
    assert (await _run(env, reg, pid2))["status"] == "succeeded"


# --- журнал -----------------------------------------------------------------

async def test_history_is_append_only_and_records_estimate_vs_recorded(env) -> None:
    async def paid(ctx, params):
        return {"credits_spent": 7.5}

    reg = fresh_registry(make_spec("t.paid", paid, risk="paid", spends=True, cost=(5, 10)))
    pid = await _plan(env, reg, {"action_id": "t.paid", "params": {"n": 3}}, text="нарисуй")
    await _run(env, reg, pid, approve=True)
    items = (await service.history(plan_id=pid))["items"]
    events = [h["event"] for h in reversed(items)]
    assert events == ["plan_created", "approval_created", "approval_consumed", "run_requested",
                      "plan_started", "step_started", "step_succeeded", "plan_finished"]
    done = next(h for h in items if h["event"] == "step_succeeded")
    assert done["actor"] == OP.id and done["params"] == {"n": 3}
    assert done["cost_estimate"] == {"low": 5.0, "high": 10.0} and done["cost_recorded"] == 7.5
    assert next(h for h in items if h["event"] == "plan_created")["request_text"] == "нарисуй"

    async with env.factory() as s:
        with pytest.raises(DBAPIError):
            await s.execute(text("UPDATE action_layer_history SET outcome='x'"))
    async with env.factory() as s:
        with pytest.raises(DBAPIError):
            await s.execute(text("DELETE FROM action_layer_history"))
    async with env.factory() as s:
        with pytest.raises(DBAPIError):
            await s.execute(text("UPDATE action_layer_spend SET credits=0"))
    assert (await service.history(plan_id=pid))["total"] == len(items)


# --- исполнитель --------------------------------------------------------------

async def test_step_failure_stops_plan_and_never_retries(env) -> None:
    calls: list = []

    async def boom(ctx, params):
        calls.append(1)
        raise RuntimeError("provider exploded")

    async def after(ctx, params):
        calls.append("after")
        return {}

    reg = fresh_registry(make_spec("t.boom", boom, risk="local_safe"), make_spec("t.after", after))
    pid = await _plan(env, reg, {"action_id": "t.boom"}, {"action_id": "t.after"})
    view = await _run(env, reg, pid)
    assert view["status"] == "failed" and calls == [1]
    assert _statuses(view) == ["failed", "skipped"]
    assert "provider exploded" in view["steps"][0]["error"]


async def test_preconditions_are_rechecked_before_each_step(env) -> None:
    from app.services.action_layer.spec import Precondition
    ran: list = []
    state = {"ok": True}

    async def check(ctx, params):
        return None if state["ok"] else "состояние изменилось"

    async def first(ctx, params):
        state["ok"] = False
        return {}

    async def second(ctx, params):
        ran.append("second")
        return {}

    reg = fresh_registry(
        make_spec("t.first", first, risk="local_safe"),
        make_spec("t.second", second, risk="local_safe",
                  preconditions=(Precondition("still_ok", "ok", check),)))
    pid = await _plan(env, reg, {"action_id": "t.first"}, {"action_id": "t.second"})
    view = await _run(env, reg, pid)
    assert ran == [] and view["status"] == "failed"
    assert "still_ok" in view["steps"][1]["error"]


async def test_dependency_not_satisfied_skips_step(env) -> None:
    async def ex(ctx, params):
        return {}

    reg = fresh_registry(make_spec("t.ex", ex))
    pid = await _plan(env, reg, {"action_id": "t.ex"}, {"action_id": "t.ex", "depends_on": [0]})
    async with env.factory() as s:  # шаг 0 «не удался» заранее
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.plan_id == pid, store.ActionPlanStep.idx == 0
        ).values(status="failed"))
        await s.commit()
    view = await _run(env, reg, pid)
    assert view["status"] == "failed"


async def test_idempotency_key_is_unique_per_step(env) -> None:
    async def ex(ctx, params):
        return {}

    reg = fresh_registry(make_spec("t.ex", ex))
    pid = await _plan(env, reg, {"action_id": "t.ex"}, {"action_id": "t.ex"})
    view = await service.plan_view(pid)
    assert len(view["steps"]) == 2
    async with env.factory() as s:
        keys = (await s.scalars(select(store.ActionPlanStep.idempotency_key))).all()
        assert sorted(keys) == [f"{pid}:0", f"{pid}:1"]
        s.add(store.ActionPlanStep(plan_id=pid, idx=9, action_id="t.ex",
                                   idempotency_key=f"{pid}:0"))
        with pytest.raises(Exception, match="UNIQUE"):
            await s.commit()


async def test_stop_between_steps(env) -> None:
    ran: list = []
    holder: dict = {}

    async def first(ctx, params):
        ran.append("first")
        await executor.request_stop(holder["pid"], "op")
        return {}

    async def second(ctx, params):
        ran.append("second")
        return {}

    reg = fresh_registry(make_spec("t.first", first, risk="local_safe"),
                         make_spec("t.second", second, risk="local_safe"))
    pid = holder["pid"] = await _plan(env, reg, {"action_id": "t.first"}, {"action_id": "t.second"})
    view = await _run(env, reg, pid)
    assert ran == ["first"] and view["status"] == "stopped"
    assert _statuses(view) == ["succeeded", "skipped"]


async def test_stop_calls_job_stop_endpoint_and_reports_progress(env) -> None:
    state = {"polls": 0, "stopped": False, "stop_calls": 0}

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        state["polls"] += 1
        if state["stopped"]:
            return JobState(done=True, ok=False, error="cancelled")
        return JobState(done=False, progress=0.25, note="идёт")

    async def stop(ctx, params):
        state["stop_calls"] += 1
        state["stopped"] = True

    reg = fresh_registry(make_spec("t.job", start, risk="local_safe", poll=poll, stop=stop,
                                   poll_interval_s=0.01))
    pid = await _plan(env, reg, {"action_id": "t.job"}, {"action_id": "t.job"})
    await service.prepare_run(pid, OP, registry=reg)
    executor.launch(env.app, pid, OP.id, registry=reg)
    for _ in range(200):
        view = await service.plan_view(pid)
        if view["steps"][0]["progress"] == 0.25:
            break
        await asyncio.sleep(0.01)
    assert view["steps"][0]["progress_note"] == "идёт" and view["live"]
    result = await executor.request_stop(pid, "op")
    assert result["stop_requested"] is True
    await executor.wait(pid)
    view = await service.plan_view(pid)
    assert state["stop_calls"] == 1
    assert view["status"] == "stopped" and _statuses(view) == ["cancelled", "skipped"]


async def test_job_runs_to_completion(env) -> None:
    n = {"i": 0}

    async def start(ctx, params):
        return {"job": "j1"}

    async def poll(ctx, params, started):
        n["i"] += 1
        if n["i"] < 3:
            return JobState(done=False, progress=n["i"] / 3)
        return JobState(done=True, result={"files": 2}, credits_spent=3.0)

    reg = fresh_registry(make_spec("t.job", start, risk="paid", spends=True, cost=(1, 5),
                                   poll=poll, poll_interval_s=0.01))
    pid = await _plan(env, reg, {"action_id": "t.job"})
    view = await _run(env, reg, pid, approve=True)
    assert view["status"] == "succeeded"
    assert view["steps"][0]["result"] == {"job": "j1", "files": 2}
    assert view["spent_credits"] == pytest.approx(3.0)


async def _interrupt(env, pid, *, done=(), running=()):
    """Имитация падения процесса: план running, часть шагов завершена, часть в полёте."""
    async with env.factory() as s:
        await s.execute(update(store.ActionPlan).where(store.ActionPlan.id == pid).values(
            status="running"))
        for idx in done:
            await s.execute(update(store.ActionPlanStep).where(
                store.ActionPlanStep.plan_id == pid, store.ActionPlanStep.idx == idx
            ).values(status="succeeded"))
        for idx in running:
            await s.execute(update(store.ActionPlanStep).where(
                store.ActionPlanStep.plan_id == pid, store.ActionPlanStep.idx == idx
            ).values(status="running"))
        await s.commit()


async def test_resume_skips_done_steps_and_retries_idempotent_inflight(env) -> None:
    ran: list = []

    def make(name):
        async def ex(ctx, params):
            ran.append(name)
            return {}
        return ex

    reg = fresh_registry(make_spec("t.a", make("a"), risk="local_safe", idempotent=True),
                         make_spec("t.b", make("b"), risk="local_safe", idempotent=True),
                         make_spec("t.c", make("c"), risk="local_safe"))
    pid = await _plan(env, reg, {"action_id": "t.a"}, {"action_id": "t.b"}, {"action_id": "t.c"})
    await _interrupt(env, pid, done=[0], running=[1])
    view = await service.plan_view(pid)
    assert view["interrupted"] is True
    await service.prepare_run(pid, OP, registry=reg)
    executor.launch(env.app, pid, OP.id, registry=reg)
    await executor.wait(pid)
    view = await service.plan_view(pid)
    assert ran == ["b", "c"] and view["status"] == "succeeded"


async def test_inflight_non_idempotent_step_is_never_rerun(env) -> None:
    ran: list = []

    async def swap(ctx, params):
        ran.append("swap")
        return {}

    async def tail(ctx, params):
        ran.append("tail")
        return {}

    reg = fresh_registry(make_spec("t.swap", swap, risk="local_safe", idempotent=False),
                         make_spec("t.tail", tail, risk="local_safe"))
    pid = await _plan(env, reg, {"action_id": "t.swap"}, {"action_id": "t.tail"})
    await _interrupt(env, pid, running=[0])
    await service.prepare_run(pid, OP, registry=reg)
    executor.launch(env.app, pid, OP.id, registry=reg)
    await executor.wait(pid)
    view = await service.plan_view(pid)
    assert ran == [] and view["status"] == "failed"
    assert _statuses(view) == ["unknown", "skipped"]
    assert "ручной разбор" in view["steps"][0]["error"]


async def test_resume_of_paid_plan_needs_prior_approval_record(env) -> None:
    async def paid(ctx, params):
        return {}

    reg = fresh_registry(make_spec("t.paid", paid, risk="paid", spends=True, cost=(1, 2)))
    pid = await _plan(env, reg, {"action_id": "t.paid"})
    await _interrupt(env, pid)  # «running», но подтверждение не расходовалось
    with pytest.raises(approval.ApprovalError):
        await service.prepare_run(pid, OP, registry=reg)


async def test_one_live_plan_per_project(env) -> None:
    gate = asyncio.Event()

    async def slow(ctx, params):
        await gate.wait()
        return {}

    schema = {"type": "object", "properties": {"project_id": {"type": "integer"}},
              "additionalProperties": False}
    reg = fresh_registry(make_spec("t.slow", slow, risk="local_safe", schema=schema))
    p1 = await _plan(env, reg, {"action_id": "t.slow", "params": {"project_id": 5}})
    p2 = await _plan(env, reg, {"action_id": "t.slow", "params": {"project_id": 5}})
    await service.prepare_run(p1, OP, registry=reg)
    executor.launch(env.app, p1, OP.id, registry=reg)
    await asyncio.sleep(0.05)
    with pytest.raises(service.RunRefused) as e:
        await service.prepare_run(p2, OP, registry=reg)
    assert e.value.code == "project_busy"
    gate.set()
    await executor.wait(p1)
    assert (await service.plan_view(p1))["status"] == "succeeded"
