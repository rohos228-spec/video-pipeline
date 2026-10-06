"""Контур изменений через слой действий: подтверждение оператора обязательно, планировщик не применяет."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Project
from app.services.action_layer import approval, executor, planner, store
from app.services.action_layer import service as al_service
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.services.change_control import service
from app.services.change_control import store as cs
from app.services.change_control.errors import ChangeRefused
from app.services.change_control.gateway import CtxGateway
from tests.action_layer_support import env  # noqa: F401
from tests.change_control_support import ccenv, prompts_root  # noqa: F401

AL = "/api/action-layer"
CC = "/api/change-control"


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


def _split_body(pid, value=600):
    return {"tier": "T1", "kind": "step_params", "project_id": pid, "node_key": "n_split",
            "rationale": "сократить ячейки",
            "candidate": {"step": "split", "values": {"cell_max_chars": value}}}


async def _propose_rest(env, **over):
    r = await env.client.post(f"{CC}/proposals", json={**_split_body(env.pid), **over})
    assert r.status_code == 200, r.text
    return r.json()


async def _preview(env, steps):
    return await env.client.post(f"{AL}/plans/preview", json={"steps": steps})


async def _plan(env, steps):
    r = await _preview(env, steps)
    assert r.status_code == 200, r.text
    return r.json()["plan_id"], r.json()["preview"]


async def _invalid(env, steps) -> str:
    r = await _preview(env, steps)
    assert r.status_code == 422, r.text
    return str(r.json())


async def _run(env, plan_id, *, approve=True):
    if approve:
        a = await env.client.post(f"{AL}/plans/{plan_id}/approve")
        assert a.status_code == 200, a.text
    assert (await env.client.post(f"{AL}/plans/{plan_id}/run")).status_code == 200
    await executor.wait(plan_id)
    return (await env.client.get(f"{AL}/plans/{plan_id}")).json()


def _apply_step(view):
    return {"action_id": "change.apply",
            "params": {"proposal_id": view["id"], "expected_digest": view["digest"]}}


async def _split_value(env):
    async with env.factory() as s:
        p = await s.get(Project, env.pid)
        return p.meta["node_step_params"]["split"]["cell_max_chars"]


async def _history(env):
    async with env.factory() as s:
        rows = (await s.scalars(select(store.ActionHistory).order_by(store.ActionHistory.id))).all()
    return [h for h in rows if h.event.startswith("change_")]


# --- каталог -----------------------------------------------------------------------------------

async def test_catalog_has_the_change_actions_with_the_right_tiers(ccenv) -> None:
    expect = {"change.list": ("read", False), "change.get": ("read", False),
              "change.propose": ("local_safe", False), "change.apply": ("destructive", True),
              "change.rollback": ("destructive", True)}
    for aid, (risk, needs) in expect.items():
        spec = registry.get(aid)
        assert spec.risk == risk and spec.needs_approval is needs, aid
        assert spec.spends_money is False
        est = spec.estimate({})
        assert est.known and est.low == 0 and est.high == 0, aid
    assert registry.get("change.apply").destructive_effects is not None
    r = await ccenv.client.get(f"{AL}/catalog", params={"prefix": "change."})
    assert r.json()["count"] == 5
    assert {a["id"] for a in r.json()["actions"]} == set(expect)


async def test_the_planner_cannot_smuggle_identity_or_approval_fields(ccenv) -> None:
    view = await _propose_rest(ccenv)
    for extra in ({"approved_by": "x"}, {"proposer": "human"}, {"approval_id": "x"}):
        step = _apply_step(view)
        step["params"].update(extra)
        assert "лишнее поле" in await _invalid(ccenv, [step])
    body = _split_body(ccenv.pid)
    assert "лишнее поле" in await _invalid(ccenv, [{
        "action_id": "change.propose", "params": {**body, "proposer": "human"}}])


# --- одобрение ------------------------------------------------------------------------------------

async def test_a_plan_with_change_apply_needs_approval_and_does_nothing_without_it(ccenv) -> None:
    view = await _propose_rest(ccenv)
    plan_id, preview = await _plan(ccenv, [_apply_step(view)])
    assert preview["needs_approval"] is True and preview["risk_max"] == "destructive"
    assert preview["cost"]["low"] == 0 and preview["cost"]["high"] == 0
    assert "контрольная сумма" in preview["destructive_effects"][0]["text"]
    r = await ccenv.client.post(f"{AL}/plans/{plan_id}/run")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "approval_required"
    assert await _split_value(ccenv) == 400
    async with ccenv.factory() as s:
        assert (await s.get(cs.ChangeProposal, view["id"])).status == "proposed"


async def test_apply_and_rollback_through_approved_plans(ccenv) -> None:
    view = await _propose_rest(ccenv)
    plan_id, _ = await _plan(ccenv, [_apply_step(view)])
    plan = await _run(ccenv, plan_id)
    assert plan["status"] == "succeeded", plan
    assert plan["steps"][0]["result"]["version"] == 2
    assert await _split_value(ccenv) == 600
    got = (await ccenv.client.get(f"{CC}/proposals/{view['id']}")).json()
    assert got["status"] == "applied" and got["approved_by"] == "local_web_operator"
    assert got["current_version"] == 2
    versions = (await ccenv.client.get(f"{CC}/versions", params={"target": view["target"]})).json()
    assert [v["kind"] for v in versions["items"]] == ["apply", "baseline"]
    # подтверждение одноразовое, повтор применения того же предложения не проходит
    assert (await ccenv.client.post(f"{AL}/plans/{plan_id}/run")).status_code == 409
    assert "status_not_applicable" in await _invalid(ccenv, [_apply_step(view)])
    # откат: отдельный план, снова с подтверждением
    rb = [{"action_id": "change.rollback", "params": {
        "target": view["target"], "expected_version": 2, "reason": "тест"}}]
    rb_id, rb_prev = await _plan(ccenv, rb)
    assert rb_prev["needs_approval"] is True
    assert (await ccenv.client.post(f"{AL}/plans/{rb_id}/run")).status_code == 403
    done = await _run(ccenv, rb_id)
    assert done["status"] == "succeeded" and done["steps"][0]["result"]["version"] == 3
    assert await _split_value(ccenv) == 400
    events = [(h.event, h.outcome) for h in await _history(ccenv)]
    assert ("change_proposed", "proposed") in events and ("change_applied", "applied") in events
    assert ("change_rolled_back", "rolled_back") in events
    assert all(h.plan_id for h in await _history(ccenv) if h.event != "change_proposed")


async def test_preview_refuses_t3_stale_and_wrong_digest(ccenv) -> None:
    t3 = (await ccenv.client.post(f"{CC}/proposals", json={
        "tier": "T3", "kind": "developer_review", "rationale": "нужен код",
        "candidate": {"summary": "правка", "diff": "-a\n+b"}})).json()
    assert t3["status"] == "developer_review"
    assert "tier_not_applicable" in await _invalid(ccenv, [_apply_step(t3)])
    view = await _propose_rest(ccenv)
    bad = _apply_step(view)
    bad["params"]["expected_digest"] = "a" * 64
    assert "digest_mismatch" in await _invalid(ccenv, [bad])
    patch = {"meta": {"node_step_params": {"split": {"cell_max_chars": 450}}}}
    assert (await ccenv.client.patch(f"/api/projects/{ccenv.pid}", json=patch)).status_code == 200
    assert "stale_base" in await _invalid(ccenv, [_apply_step(view)])
    assert await _split_value(ccenv) == 450


async def test_a_plan_made_before_another_apply_cannot_be_approved_after_it(ccenv) -> None:
    view = await _propose_rest(ccenv)
    other = await _propose_rest(ccenv, candidate={"step": "split", "values": {"cell_max_chars": 700}})
    first_plan, _ = await _plan(ccenv, [_apply_step(view)])
    second_plan, _ = await _plan(ccenv, [_apply_step(other)])
    assert (await _run(ccenv, first_plan))["status"] == "succeeded"
    a = await ccenv.client.post(f"{AL}/plans/{second_plan}/approve")
    assert a.status_code == 409 and a.json()["detail"]["code"] == "plan_invalid"
    assert await _split_value(ccenv) == 600


# --- планировщик ---------------------------------------------------------------------------------------

async def test_a_planner_plan_creates_a_planner_proposal_that_cannot_apply_itself(ccenv) -> None:
    plan_id, preview = await _plan(ccenv, [{
        "action_id": "change.propose", "params": _split_body(ccenv.pid)}])
    assert preview["needs_approval"] is False and preview["risk_max"] == "local_safe"
    ctx = await planner.load_context(ccenv.pid, None, None)
    await planner.save_plan_context(plan_id, source="planner", ctx=ctx)
    plan = await _run(ccenv, plan_id, approve=False)
    assert plan["status"] == "succeeded", plan
    proposal_id = plan["steps"][0]["result"]["proposal_id"]
    got = (await ccenv.client.get(f"{CC}/proposals/{proposal_id}")).json()
    assert got["proposer"] == "planner:local_web_operator" and got["status"] == "proposed"
    assert await _split_value(ccenv) == 400                      # предложение ничего не меняет
    gw = CtxGateway(ActionContext(app=ccenv.app, operator="x"))
    # само себя оно применить не может: ни напрямую, ни своим планом без подтверждения
    with pytest.raises(ChangeRefused) as exc:
        await service.apply(gw, proposal_id, got["digest"], approved_by=got["proposer"])
    assert exc.value.code == "operator_required"
    apply_plan, _ = await _plan(ccenv, [_apply_step(got)])
    assert (await ccenv.client.post(f"{AL}/plans/{apply_plan}/run")).status_code == 403
    with pytest.raises(approval.ApprovalError) as exc2:
        await al_service.approve_plan(ccenv.app, apply_plan, approval.OperatorIdentity("planner:x"))
    assert exc2.value.code == "operator_required"
    assert await _split_value(ccenv) == 400
    # человек подтверждает отдельным вызовом - и только тогда применяется
    done = await _run(ccenv, apply_plan)
    assert done["status"] == "succeeded" and await _split_value(ccenv) == 600


async def test_a_human_plan_records_the_human_as_proposer(ccenv) -> None:
    plan_id, _ = await _plan(ccenv, [{
        "action_id": "change.propose", "params": _split_body(ccenv.pid)}])
    plan = await _run(ccenv, plan_id, approve=False)
    got = (await ccenv.client.get(f"{CC}/proposals/{plan['steps'][0]['result']['proposal_id']}")).json()
    assert got["proposer"] == "local_web_operator"


async def test_propose_preview_reports_lint_problems_before_anything_is_stored(ccenv) -> None:
    text = await _invalid(ccenv, [{"action_id": "change.propose",
                                   "params": _split_body(ccenv.pid, 5000)}])
    assert "proposal_valid" in text and "lint_failed" in text
    async with ccenv.factory() as s:
        assert list((await s.scalars(select(cs.ChangeProposal))).all()) == []


async def test_read_actions_list_and_get(ccenv) -> None:
    view = await _propose_rest(ccenv)
    plan_id, _ = await _plan(ccenv, [
        {"action_id": "change.list", "params": {"project_id": ccenv.pid, "status": "proposed"}},
        {"action_id": "change.get", "params": {"proposal_id": view["id"]}}])
    plan = await _run(ccenv, plan_id, approve=False)
    assert plan["status"] == "succeeded", plan
    assert plan["steps"][0]["result"]["count"] == 1
    assert plan["steps"][1]["result"]["id"] == view["id"]
    assert "one_selector" in await _invalid(ccenv, [{"action_id": "change.get", "params": {}}])
