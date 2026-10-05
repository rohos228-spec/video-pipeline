"""Подтверждение: привязка к хэшу, срок, одноразовость, запрет для модели."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.services.action_layer import approval, executor, service, store
from tests.action_layer_support import env, fresh_registry, make_spec  # noqa: F401

OP = approval.OperatorIdentity("web_operator:abc123")


async def _ex(ctx, params):
    return {"ran": True}


def _reg():
    return fresh_registry(
        make_spec("t.read", _ex),
        make_spec("t.local", _ex, risk="local_safe"),
        make_spec("t.paid", _ex, risk="paid", spends=True, cost=(5, 10)),
        make_spec("t.kill", _ex, risk="destructive", destructive=True))


async def _new_plan(env, reg, *steps):
    plan_id, preview = await service.create_plan(env.app, list(steps), operator=OP,
                                                 request_text="сделай", registry=reg)
    assert plan_id, preview
    return plan_id


async def test_read_and_local_plans_need_no_approval(env) -> None:
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.read"}, {"action_id": "t.local"})
    await service.prepare_run(pid, OP, registry=reg)  # без approve
    executor.launch(env.app, pid, OP.id, registry=reg)
    await executor.wait(pid)
    view = await service.plan_view(pid)
    assert view["status"] == "succeeded" and not view["needs_approval"]
    async with env.factory() as s:
        assert await s.scalar(select(store.ActionApproval)) is None
    with pytest.raises(approval.ApprovalError) as e:
        async with env.factory() as s:
            row = await s.get(store.ActionPlan, pid)
            await approval.create_approval(s, row, OP)
    assert e.value.code in ("approval_not_needed", "plan_not_approvable")


@pytest.mark.parametrize("step", [{"action_id": "t.paid"}, {"action_id": "t.kill"}])
async def test_run_without_approval_is_refused(env, step) -> None:
    reg = _reg()
    pid = await _new_plan(env, reg, step)
    with pytest.raises(approval.ApprovalError) as e:
        await service.prepare_run(pid, OP, registry=reg)
    assert e.value.code == "approval_required"
    assert (await service.plan_view(pid))["status"] == "proposed"


async def test_approval_is_single_use_and_run_consumes_it(env) -> None:
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.paid"})
    info = await service.approve_plan(env.app, pid, OP, registry=reg)
    assert info["plan_hash"]
    await service.prepare_run(pid, OP, registry=reg)
    executor.launch(env.app, pid, OP.id, registry=reg)
    await executor.wait(pid)
    view = await service.plan_view(pid)
    assert view["status"] == "succeeded"
    assert view["approvals"][0]["used_at"] and not view["approvals"][0]["active"]
    # Повторный запуск того же плана: он завершён, нужен новый план.
    with pytest.raises(service.RunRefused):
        await service.prepare_run(pid, OP, registry=reg)


async def test_second_run_after_stop_needs_fresh_approval(env) -> None:
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.paid"})
    await service.approve_plan(env.app, pid, OP, registry=reg)
    await service.prepare_run(pid, OP, registry=reg)  # расходует
    await executor.request_stop(pid, OP.id)           # не живой -> stopped
    assert (await service.plan_view(pid))["status"] == "stopped"
    with pytest.raises(approval.ApprovalError):
        await service.prepare_run(pid, OP, registry=reg)
    await service.approve_plan(env.app, pid, OP, registry=reg)  # новое подтверждение
    await service.prepare_run(pid, OP, registry=reg)
    executor.launch(env.app, pid, OP.id, registry=reg)
    await executor.wait(pid)
    assert (await service.plan_view(pid))["status"] == "succeeded"


async def test_approval_expires(env) -> None:
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.paid"})
    await service.approve_plan(env.app, pid, OP, registry=reg, ttl_s=60)
    async with env.factory() as s:
        await s.execute(update(store.ActionApproval).values(
            expires_at=datetime.utcnow() - timedelta(seconds=1)))
        await s.commit()
    with pytest.raises(approval.ApprovalError) as e:
        await service.prepare_run(pid, OP, registry=reg)
    assert e.value.code == "approval_required"


async def test_default_ttl_comes_from_settings(env, monkeypatch) -> None:
    from app.settings import settings
    assert settings.action_layer_approval_ttl_s == 600
    monkeypatch.setattr(settings, "action_layer_approval_ttl_s", 42)
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.paid"})
    await service.approve_plan(env.app, pid, OP, registry=reg)
    async with env.factory() as s:
        a = await s.scalar(select(store.ActionApproval))
        assert 40 <= (a.expires_at - a.created_at).total_seconds() <= 44


async def test_approval_is_bound_to_exact_plan_hash(env) -> None:
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.paid"})
    other = await _new_plan(env, reg, {"action_id": "t.paid", "params": {"n": 1}})
    await service.approve_plan(env.app, pid, OP, registry=reg)
    # Подтверждение чужого плана не помогает.
    with pytest.raises(approval.ApprovalError):
        await service.prepare_run(other, OP, registry=reg)
    # Подмена содержимого плана после подтверждения: хэш не сходится.
    async with env.factory() as s:
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.plan_id == pid).values(params={"n": 99}))
        await s.commit()
    with pytest.raises(service.RunRefused) as e:
        await service.prepare_run(pid, OP, registry=reg)
    assert e.value.code == "plan_changed"
    # И сама привязка в БД: подтверждение с другим хэшем не расходуется.
    async with env.factory() as s:
        await s.execute(update(store.ActionApproval).values(plan_hash="0" * 64))
        await s.execute(update(store.ActionPlanStep).where(
            store.ActionPlanStep.plan_id == pid).values(params={}))
        await s.commit()
    with pytest.raises(approval.ApprovalError):
        await service.prepare_run(pid, OP, registry=reg)


async def test_model_or_system_identity_cannot_approve(env) -> None:
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.paid"})
    for who in ("planner:gpt", "model:x", "agent:y", "system", ""):
        with pytest.raises(approval.ApprovalError) as e:
            await service.approve_plan(env.app, pid, approval.OperatorIdentity(who), registry=reg)
        assert e.value.code == "operator_required"
    with pytest.raises(approval.ApprovalError):  # не OperatorIdentity вообще
        async with env.factory() as s:
            row = await s.get(store.ActionPlan, pid)
            await approval.create_approval(s, row, "planner:gpt")  # type: ignore[arg-type]
    async with env.factory() as s:
        assert await s.scalar(select(store.ActionApproval)) is None


async def test_new_approval_supersedes_old_one(env) -> None:
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.paid"})
    a1 = await service.approve_plan(env.app, pid, OP, registry=reg)
    a2 = await service.approve_plan(env.app, pid, OP, registry=reg)
    view = await service.plan_view(pid)
    active = [a["id"] for a in view["approvals"] if a["active"]]
    assert active == [a2["approval_id"]] and a1["approval_id"] != a2["approval_id"]


async def test_over_budget_plan_cannot_be_approved(env, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 7.0)
    reg = _reg()
    pid = await _new_plan(env, reg, {"action_id": "t.paid"})  # high=10 > 7
    with pytest.raises(approval.ApprovalError) as e:
        await service.approve_plan(env.app, pid, OP, registry=reg)
    assert e.value.code == "over_budget"
