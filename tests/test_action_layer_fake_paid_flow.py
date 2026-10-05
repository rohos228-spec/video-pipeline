"""Полный платный/разрушающий поток на фейковых test.* действиях (без денег и сети).

Фейки живут в scripts/e2e_action_layer_fake_paid.py (тестовый запускатор) и
регистрируются только в отдельный реестр теста либо при ACTION_LAYER_TEST_ACTIONS=1.
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import socket
from pathlib import Path

import pytest
from sqlalchemy import update

from app.services import kill_switch
from app.services.action_layer import approval, executor, service, store
from app.services.action_layer.spec import ActionRegistry, registry as default_registry
from tests.action_layer_support import env, fresh_registry  # noqa: F401

OP = approval.OperatorIdentity("web_operator:fakepaid")
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_action_layer_fake_paid.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("e2e_fake_paid_launcher", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTION_LAYER_TEST_ACTIONS", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    mod = _load_launcher()
    mod.JOBS.clear()

    # Быстрый опрос вместо 1 с, поведение действий прежнее.
    specs = [dataclasses.replace(s, poll_interval_s=0.02) for s in mod.build_fake_specs()]
    reg = ActionRegistry()
    for s in specs:
        reg.register(s)
    mod.reg = reg
    mod.events = lambda: [json.loads(x) for x in
                          (tmp_path / "fake_jobs.jsonl").read_text("utf-8").splitlines()
                          ] if (tmp_path / "fake_jobs.jsonl").exists() else []
    return mod


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"network call to {address!r} in a fake-action test")
        return real(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded)


async def _plan(env, fake, *steps, text="тест"):
    pid, preview = await service.create_plan(env.app, list(steps), operator=OP,
                                             request_text=text, registry=fake.reg)
    return pid, preview


async def _approve_run(env, fake, pid):
    await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    await service.prepare_run(pid, OP, registry=fake.reg)
    executor.launch(env.app, pid, OP.id, registry=fake.reg)


async def _wait_status(pid, want, timeout=5.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        v = await service.plan_view(pid)
        if v["status"] in want and not v["live"]:
            return v
        await asyncio.sleep(0.03)
    raise AssertionError(f"plan never reached {want}: {await service.plan_view(pid)}")


async def _wait_progress(pid, minimum=0.05, timeout=5.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        v = await service.plan_view(pid)
        if (v["steps"][0]["progress"] or 0) >= minimum:
            return v
        await asyncio.sleep(0.03)
    raise AssertionError("no progress")


# --- реестр и защита --------------------------------------------------------

def test_launcher_refuses_without_flag(monkeypatch) -> None:
    monkeypatch.delenv("ACTION_LAYER_TEST_ACTIONS", raising=False)
    mod = _load_launcher()
    with pytest.raises(mod.FakeActionsDisabled):
        mod.register_fake_actions(ActionRegistry())
    assert mod.main([]) == 2
    monkeypatch.setenv("ACTION_LAYER_TEST_ACTIONS", "0")
    with pytest.raises(mod.FakeActionsDisabled):
        mod.register_fake_actions(ActionRegistry())
    assert not any(s.id.startswith("test.") for s in default_registry.list())


def test_fake_actions_are_namespaced_and_flagged_correctly(fake) -> None:
    by_id = {s.id: s for s in fake.reg.list()}
    assert set(by_id) == set(fake.FAKE_ACTION_IDS)
    assert all(i.startswith("test.") for i in by_id)
    job, inst, dest = by_id["test.fake_paid_job"], by_id["test.fake_paid_instant"], by_id["test.fake_destructive"]
    assert job.spends_money and job.destructive and job.risk == "destructive" and job.is_job and job.stop
    assert inst.risk == "paid" and inst.spends_money and not inst.destructive and not inst.is_job
    assert dest.risk == "destructive" and not dest.spends_money
    assert (job.estimate({}).low, job.estimate({}).high) == (40.0, 60.0)
    assert all(not s.idempotent for s in by_id.values())


# --- A: мгновенное платное --------------------------------------------------

async def test_paid_instant_needs_approval_then_spends_42(env, fake) -> None:
    pid, preview = await _plan(env, fake, {"action_id": "test.fake_paid_instant"})
    assert preview["needs_approval"] and preview["cost"]["high"] == 50.0
    assert preview["budget"]["fits"] and preview["budget"]["remaining_credits"] == 200.0
    with pytest.raises(approval.ApprovalError) as e:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e.value.code == "approval_required"
    assert fake.events() == []  # ничего не исполнено

    await _approve_run(env, fake, pid)
    view = await _wait_status(pid, {"succeeded"})
    assert view["spent_credits"] == pytest.approx(42.0)
    assert view["steps"][0]["cost"] == {"low": 30.0, "high": 50.0, "actual": 42.0}
    events = [h["event"] for h in reversed((await service.history(plan_id=pid))["items"])]
    assert events == ["plan_created", "approval_created", "approval_consumed", "run_requested",
                      "plan_started", "step_started", "step_succeeded", "plan_finished"]
    assert [e["event"] for e in fake.events()] == ["instant_paid"]

    # Повторы: запуск и новое подтверждение завершённого плана отвергаются.
    with pytest.raises(service.RunRefused) as e1:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e1.value.code == "plan_finished"
    with pytest.raises(approval.ApprovalError) as e2:
        await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    assert e2.value.code == "plan_not_approvable"


async def test_fake_destructive_free_but_needs_approval(env, fake) -> None:
    pid, preview = await _plan(env, fake, {"action_id": "test.fake_destructive"})
    assert preview["cost"]["high"] == 0.0 and preview["needs_approval"]
    assert preview["destructive_effects"] and "ТЕСТ" in preview["destructive_effects"][0]["text"]
    with pytest.raises(approval.ApprovalError):
        await service.prepare_run(pid, OP, registry=fake.reg)
    await _approve_run(env, fake, pid)
    view = await _wait_status(pid, {"succeeded"})
    assert view["spent_credits"] == 0.0


# --- B: фоновая задача, стоп, повторный запуск ------------------------------

async def test_job_stop_midrun_stops_fake_job_and_reuse_needs_new_approval(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 60}})
    await _approve_run(env, fake, pid)
    v = await _wait_progress(pid)
    assert v["status"] == "running" and v["steps"][0]["status"] == "running"
    assert "тестовая задача" in v["steps"][0]["progress_note"]
    assert v["spent_credits"] == 60.0  # резерв верхней оценки

    out = await executor.request_stop(pid, OP.id)
    assert out["stop_requested"] is True
    view = await _wait_status(pid, {"stopped"})
    assert view["stop_reason"] == "operator_stop"
    assert view["steps"][0]["status"] == "cancelled"
    job = next(iter(fake.JOBS.values()))
    assert job["stopped"] is True and not job["finished"]
    assert [e["event"] for e in fake.events()] == ["started", "stopped"]
    # Резерв остаётся (факт неизвестен): консервативно, не 0 и не оценка по прогрессу.
    assert view["spent_credits"] == 60.0
    events = [h["event"] for h in (await service.history(plan_id=pid))["items"]]
    assert {"stop_requested", "step_cancelled", "plan_finished"} <= set(events)

    # Тот же план: подтверждение прежнего запуска израсходовано, нужен НОВЫЙ approve.
    with pytest.raises(approval.ApprovalError) as e:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e.value.code == "approval_required"
    assert len(fake.events()) == 2  # ничего не стартовало


async def test_job_runs_to_completion_and_reports_credits(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 0.5}})
    await _approve_run(env, fake, pid)
    view = await _wait_status(pid, {"succeeded"})
    assert view["steps"][0]["cost"]["actual"] == 47.5
    # резерв 60 сверен с фактом 47.5
    assert view["spent_credits"] == pytest.approx(47.5)
    assert view["steps"][0]["progress"] == 1.0
    assert [e["event"] for e in fake.events()] == ["started", "finished"]


# --- C: одноразовость, срок, хэш, потолок -----------------------------------

async def test_over_session_cap_cannot_be_approved(env, fake) -> None:
    pid, preview = await _plan(env, fake, *[{"action_id": "test.fake_paid_instant"}] * 5)
    assert preview["cost"]["high"] == 250.0 and preview["budget"]["fits"] is False
    with pytest.raises(approval.ApprovalError) as e:
        await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    assert e.value.code == "over_budget"
    with pytest.raises(approval.ApprovalError) as e2:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e2.value.code == "approval_required"
    assert fake.events() == []


async def test_expired_approval_refused(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_instant"})
    await service.approve_plan(env.app, pid, OP, registry=fake.reg, ttl_s=0)
    with pytest.raises(approval.ApprovalError) as e:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e.value.code == "approval_required"
    assert fake.events() == []


async def test_approval_is_single_use_under_concurrent_runs(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_instant"})
    await service.approve_plan(env.app, pid, OP, registry=fake.reg)

    async def attempt():
        try:
            await service.prepare_run(pid, OP, registry=fake.reg)
            executor.launch(env.app, pid, OP.id, registry=fake.reg)
            return "started"
        except (approval.ApprovalError, service.RunRefused) as exc:
            return exc.code

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results)[1] == "started" and results.count("started") == 1
    await _wait_status(pid, {"succeeded"})
    assert [e["event"] for e in fake.events()] == ["instant_paid"]


async def test_new_approval_revokes_previous_and_only_one_is_consumed(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_instant"})
    await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    view = await service.plan_view(pid)
    assert [a["active"] for a in view["approvals"]] == [True, False]
    await _approve_run_noapprove(env, fake, pid)
    view = await _wait_status(pid, {"succeeded"})
    assert sum(1 for a in view["approvals"] if a["used_at"]) == 1


async def _approve_run_noapprove(env, fake, pid):
    await service.prepare_run(pid, OP, registry=fake.reg)
    executor.launch(env.app, pid, OP.id, registry=fake.reg)


async def test_changed_plan_hash_refused_at_approve_and_run(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_instant"})
    await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    async with env.factory() as s:  # подмена шага после подтверждения
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.plan_id == pid).values(action_id="test.fake_destructive"))
        await s.commit()
    with pytest.raises(service.RunRefused) as e:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e.value.code == "plan_changed"
    with pytest.raises(approval.ApprovalError) as e2:
        await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    assert e2.value.code == "plan_changed"
    assert fake.events() == []


async def test_approval_bound_to_other_hash_is_not_consumable(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_instant"})
    await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    async with env.factory() as s:
        await s.execute(update(store.ActionApproval).where(
            store.ActionApproval.plan_id == pid).values(plan_hash="0" * 64))
        await s.commit()
    with pytest.raises(approval.ApprovalError) as e:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e.value.code == "approval_required"


async def test_machine_identities_cannot_approve(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_instant"})
    for who in ("planner:x", "model:y", "agent:z", "system"):
        with pytest.raises(approval.ApprovalError) as e:
            await service.approve_plan(env.app, pid, approval.OperatorIdentity(who), registry=fake.reg)
        assert e.value.code == "operator_required"


# --- D: рестарт -------------------------------------------------------------

async def _simulate_restart_midrun(env, fake, pid):
    """approve+prepare_run (расход подтверждения), шаг 0 помечен running, живой задачи нет."""
    await service.approve_plan(env.app, pid, OP, registry=fake.reg)
    await service.prepare_run(pid, OP, registry=fake.reg)
    async with env.factory() as s:
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.plan_id == pid, store.ActionPlanStep.idx == 0
        ).values(status=store.STEP_RUNNING, attempts=1))
        await s.commit()


async def test_restart_inflight_nonidempotent_step_is_not_rerun(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 60}},
                         {"action_id": "test.fake_paid_instant"})
    await _simulate_restart_midrun(env, fake, pid)
    view = await service.plan_view(pid)
    assert view["status"] == "running" and view["interrupted"] and not view["live"]

    await service.prepare_run(pid, OP, registry=fake.reg)  # возобновление без нового подтверждения
    executor.launch(env.app, pid, OP.id, registry=fake.reg)
    view = await _wait_status(pid, {"failed", "stopped"})
    assert [s["status"] for s in view["steps"]] == ["unknown", "skipped"]
    assert "needs review" in view["error"]
    assert fake.events() == [] and not fake.JOBS  # ни задача, ни следующий шаг не запускались
    items = (await service.history(plan_id=pid))["items"]
    assert "step_unknown" in [h["event"] for h in items]
    assert next(h for h in items if h["event"] == "run_requested")["outcome"] == "resume"


async def test_stop_of_interrupted_plan_closes_it_immediately(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 60}})
    await _simulate_restart_midrun(env, fake, pid)
    out = await executor.request_stop(pid, OP.id)
    assert out["status"] == "stopped"
    assert fake.events() == []
    # Новый запуск того же плана снова требует подтверждения.
    with pytest.raises(approval.ApprovalError) as e:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e.value.code == "approval_required"


# --- E: аварийный стоп во время фоновой задачи ------------------------------

async def test_kill_switch_during_running_job_stops_the_job(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 60}},
                         {"action_id": "test.fake_paid_instant"})
    await _approve_run(env, fake, pid)
    await _wait_progress(pid)
    await kill_switch.set_state(env.factory, active=True, actor="op", rationale="stop all now")
    view = await _wait_status(pid, {"stopped"})
    assert view["stop_reason"] == kill_switch.REASON
    assert [s["status"] for s in view["steps"]] == ["cancelled", "skipped"]
    assert "kill switch" in view["steps"][0]["error"]
    # Фейковая задача реально получила stop и не дошла до конца.
    job = next(iter(fake.JOBS.values()))
    assert job["stopped"] is True and not job["finished"]
    assert [e["event"] for e in fake.events()] == ["started", "stopped"]
    assert view["spent_credits"] == 60.0  # резерв сохраняется
    events = [h["event"] for h in (await service.history(plan_id=pid))["items"]]
    assert "kill_switch_block" in events and "step_cancelled" in events
    with pytest.raises(service.RunRefused) as e:
        pid2, _ = await _plan(env, fake, {"action_id": "test.fake_paid_instant"})
        await service.approve_plan(env.app, pid2, OP, registry=fake.reg)
        await service.prepare_run(pid2, OP, registry=fake.reg)
    assert e.value.code == "kill_switch_active"
    await kill_switch.set_state(env.factory, active=False, actor="op", rationale="resume ok")


async def test_kill_switch_does_not_touch_finished_job(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 0.5}})
    await _approve_run(env, fake, pid)
    view = await _wait_status(pid, {"succeeded"})
    assert view["actual_credits"] == 47.5 and view["reserved_credits"] == pytest.approx(47.5)


# --- окно возобновления ------------------------------------------------------

async def _age(env, pid, seconds):
    from datetime import datetime, timedelta
    old = datetime.utcnow() - timedelta(seconds=seconds)
    async with env.factory() as s:
        await s.execute(update(store.ActionPlan).where(store.ActionPlan.id == pid).values(
            updated_at=old, created_at=old))
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.plan_id == pid).values(started_at=old))
        await s.commit()


async def test_resume_after_window_needs_fresh_approval(env, fake, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_resume_window_s", 3600)
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 60}},
                         {"action_id": "test.fake_paid_instant"})
    await _simulate_restart_midrun(env, fake, pid)
    await _age(env, pid, 7200)
    view = await service.plan_view(pid)
    assert view["interrupted"] and view["resume_expired"] and view["resume_window_s"] == 3600

    with pytest.raises(approval.ApprovalError) as e:
        await service.prepare_run(pid, OP, registry=fake.reg)
    assert e.value.code == "approval_required" and "Окно возобновления" in str(e.value)
    view = await service.plan_view(pid)
    assert view["status"] == "stopped" and view["stop_reason"] == "resume_window_expired"
    assert fake.events() == []
    # Без нового подтверждения запуск по-прежнему закрыт ...
    with pytest.raises(approval.ApprovalError):
        await service.prepare_run(pid, OP, registry=fake.reg)
    # ... с новым - идёт, но шаг, начатый до рестарта, повторно не запускается.
    await _approve_run(env, fake, pid)
    view = await _wait_status(pid, {"failed"})
    assert [s["status"] for s in view["steps"]] == ["unknown", "skipped"]
    assert fake.events() == []


async def test_resume_inside_window_still_works(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 60}})
    await _simulate_restart_midrun(env, fake, pid)
    await _age(env, pid, 600)
    assert (await service.plan_view(pid))["resume_expired"] is False
    await service.prepare_run(pid, OP, registry=fake.reg)
    executor.launch(env.app, pid, OP.id, registry=fake.reg)
    view = await _wait_status(pid, {"failed"})
    assert view["steps"][0]["status"] == "unknown"


async def test_list_plans_finds_running_and_interrupted(env, fake) -> None:
    pid, _ = await _plan(env, fake, {"action_id": "test.fake_paid_job", "params": {"seconds": 60}})
    await _simulate_restart_midrun(env, fake, pid)
    other, _ = await _plan(env, fake, {"action_id": "test.fake_destructive"})
    items = (await service.list_plans(status="running"))["items"]
    assert [i["id"] for i in items] == [pid] and items[0]["interrupted"] and not items[0]["live"]
    assert other in [i["id"] for i in (await service.list_plans())["items"]]
    r = await env.client.get("/api/action-layer/plans", params={"status": "running"})
    assert r.status_code == 200 and r.json()["items"][0]["id"] == pid
