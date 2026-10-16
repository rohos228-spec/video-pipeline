from __future__ import annotations

import pytest
import pytest_asyncio

from app.services import access_guard, operator_audit
from app.services.action_layer import executor
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env, make_spec

BASE = "/api/action-layer"


@pytest_asyncio.fixture
async def stubs():
    ensure_builtin_actions()
    calls: list = []

    async def read_ex(ctx, params):
        calls.append(ctx.operator)
        return {"read": True}

    spec = make_spec("zz.idem", read_ex)
    registry.register(spec)
    yield calls
    registry.unregister(spec.id)


def _plan(n: int = 1) -> dict:
    return {"steps": [{"action_id": "zz.idem", "params": {"n": n}}], "request_text": "ключ"}


async def test_same_key_and_request_creates_one_plan(env, stubs) -> None:
    h = {"Idempotency-Key": "k-preview-1"}
    first = await env.client.post(f"{BASE}/plans/preview", json=_plan(), headers=h)
    second = await env.client.post(f"{BASE}/plans/preview", json=_plan(), headers=h)
    assert first.status_code == second.status_code == 200
    assert first.json()["plan_id"] == second.json()["plan_id"]
    plans = (await env.client.get(f"{BASE}/plans")).json()
    assert len([p for p in plans["items"] if p.get("request_text") == "ключ"]) == 1


async def test_same_key_with_another_request_is_another_call(env, stubs) -> None:
    h = {"Idempotency-Key": "k-preview-2"}
    a = await env.client.post(f"{BASE}/plans/preview", json=_plan(1), headers=h)
    b = await env.client.post(f"{BASE}/plans/preview", json=_plan(2), headers=h)
    assert a.json()["plan_id"] != b.json()["plan_id"]


async def test_no_key_means_no_deduplication(env, stubs) -> None:
    a = await env.client.post(f"{BASE}/plans/preview", json=_plan())
    b = await env.client.post(f"{BASE}/plans/preview", json=_plan())
    assert a.json()["plan_id"] != b.json()["plan_id"]


async def test_run_with_a_repeated_key_is_not_executed_twice(env, stubs) -> None:
    pid = (await env.client.post(f"{BASE}/plans/preview", json=_plan())).json()["plan_id"]
    h = {"Idempotency-Key": "k-run-1"}
    one = await env.client.post(f"{BASE}/plans/{pid}/run", headers=h)
    await executor.wait(pid)
    two = await env.client.post(f"{BASE}/plans/{pid}/run", headers=h)
    assert one.status_code == two.status_code == 200 and one.json() == two.json()
    assert len(stubs) == 1
    assert (await env.client.post(f"{BASE}/plans/{pid}/run")).status_code == 409


async def test_failed_call_may_be_repeated_with_the_same_key(env, stubs) -> None:
    h = {"Idempotency-Key": "k-run-404"}
    assert (await env.client.post(f"{BASE}/plans/nope/run", headers=h)).status_code == 404
    assert (await env.client.post(f"{BASE}/plans/nope/run", headers=h)).status_code == 404


async def test_bad_key_is_rejected(env, stubs) -> None:
    r = await env.client.post(f"{BASE}/plans/preview", json=_plan(), headers={"Idempotency-Key": "bad key!"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_idempotency_key"


async def test_started_but_unfinished_call_blocks_a_blind_repeat(env, stubs) -> None:
    from app.services.action_layer import store

    from app.services.action_layer.router import PlanIn

    body = _plan()
    fp = store.call_fingerprint("POST", f"{BASE}/plans/preview", params=[], body=PlanIn(**body).model_dump())
    state, _ = await store.claim_call("k-crashed", fp, method="POST", path=f"{BASE}/plans/preview", operator="x")
    assert state == "new"
    r = await env.client.post(f"{BASE}/plans/preview", json=body, headers={"Idempotency-Key": "k-crashed"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "idempotency_unknown"


async def test_operator_header_is_recorded_in_the_call_journal(env, stubs) -> None:
    from sqlalchemy import select

    from app.services.action_layer import store

    h = {"Idempotency-Key": "k-op-1", "X-Action-Layer-Operator": "ivan petrov;forged: yes"}
    assert (await env.client.post(f"{BASE}/plans/preview", json=_plan(), headers=h)).status_code == 200
    async with env.factory() as s:
        row = await s.scalar(select(store.ActionCallRecord).where(store.ActionCallRecord.idem_key == "k-op-1"))
    assert row is not None and "\n" not in row.operator and "\r" not in row.operator
    assert row.operator == "ivan_petrov_forged:_yes"


def _scope(**headers: str) -> dict:
    return {"headers": [(k.lower().encode(), v.encode("latin-1")) for k, v in headers.items()]}


def test_operator_name_enters_the_audit_actor_only_with_a_valid_operator_token() -> None:
    token = access_guard.get_operator_token()
    named = operator_audit.resolve_actor(_scope(**{access_guard.OPERATOR_HEADER: token,
                                                   "X-Action-Layer-Operator": "ivan"}))
    assert named == "operator:ivan"
    plain = operator_audit.resolve_actor(_scope(**{access_guard.OPERATOR_HEADER: token}))
    assert plain == "operator"
    forged = operator_audit.resolve_actor(_scope(**{"X-Action-Layer-Operator": "ivan"}))
    assert forged == "local"
    wrong = operator_audit.resolve_actor(_scope(**{access_guard.OPERATOR_HEADER: "wrong",
                                                   "X-Action-Layer-Operator": "ivan"}))
    assert wrong == "local"
    unsafe = operator_audit.resolve_actor(_scope(**{access_guard.OPERATOR_HEADER: token,
                                                    "X-Action-Layer-Operator": "a b;c"}))
    assert unsafe == "operator:a_b_c"
