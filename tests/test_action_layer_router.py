"""REST /api/action-layer через ASGI-клиент (без сети, заглушки действий)."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from app.services import kill_switch
from app.services.action_layer import executor
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env, make_spec  # noqa: F401

BASE = "/api/action-layer"


@pytest_asyncio.fixture
async def stubs():
    ensure_builtin_actions()
    calls: list = []
    gate = asyncio.Event()

    async def read_ex(ctx, params):
        calls.append(("read", ctx.operator))
        return {"read": True}

    async def paid_ex(ctx, params):
        calls.append(("paid", ctx.operator, ctx.idempotency_key))
        return {"credits_spent": 3.0}

    async def slow_ex(ctx, params):
        await gate.wait()
        return {}

    specs = [make_spec("zz.read", read_ex),
             make_spec("zz.paid", paid_ex, risk="paid", spends=True, cost=(2, 5)),
             make_spec("zz.slow", slow_ex, risk="local_safe")]
    for s in specs:
        registry.register(s)
    yield {"calls": calls, "gate": gate}
    gate.set()
    for s in specs:
        registry.unregister(s.id)


async def _preview(env, *steps, text=""):
    return await env.client.post(f"{BASE}/plans/preview",
                                 json={"steps": list(steps), "request_text": text})


async def test_catalog_lists_actions_without_callables(env, stubs) -> None:
    r = await env.client.get(f"{BASE}/catalog")
    assert r.status_code == 200
    ids = {a["id"] for a in r.json()["actions"]}
    assert {"montage.get_board", "zz.paid"} <= ids
    paid = next(a for a in r.json()["actions"] if a["id"] == "zz.paid")
    assert paid["needs_approval"] and paid["spends_money"] and "executor" not in paid
    only_paid = (await env.client.get(f"{BASE}/catalog", params={"risk": "paid"})).json()
    assert {a["risk"] for a in only_paid["actions"]} == {"paid"}


async def test_preview_rejects_invalid_and_unknown_fields(env, stubs) -> None:
    r = await _preview(env, {"action_id": "zz.nope"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "plan_invalid"
    r = await _preview(env, {"action_id": "zz.read", "params": {"n": -5}})
    assert r.status_code == 422
    # Поля подтверждения в плане не принимаются.
    r = await _preview(env, {"action_id": "zz.paid", "approved": True})
    assert r.status_code == 422
    r = await env.client.post(f"{BASE}/plans/preview", json={
        "steps": [{"action_id": "zz.paid"}], "approval_id": "x"})
    assert r.status_code == 422
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": []})
    assert r.status_code == 422


async def test_preview_has_no_side_effects(env, stubs) -> None:
    r = await _preview(env, {"action_id": "zz.paid", "params": {"n": 1}}, text="нарисуй")
    assert r.status_code == 200
    body = r.json()
    assert stubs["calls"] == []
    prev = body["preview"]
    assert prev["needs_approval"] and prev["cost"]["high"] == 5.0
    assert prev["budget"]["fits"] is True and prev["plan_hash"]
    view = (await env.client.get(f"{BASE}/plans/{body['plan_id']}")).json()
    assert view["status"] == "proposed" and view["steps"][0]["status"] == "pending"


async def test_paid_flow_needs_operator_approval_then_runs_once(env, stubs) -> None:
    pid = (await _preview(env, {"action_id": "zz.paid"})).json()["plan_id"]
    r = await env.client.post(f"{BASE}/plans/{pid}/run")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "approval_required"
    # Вызывающий не может передать подтверждение сам.
    r = await env.client.post(f"{BASE}/plans/{pid}/run", json={"approval_id": "forged"})
    assert r.status_code == 422
    assert stubs["calls"] == []

    r = await env.client.post(f"{BASE}/plans/{pid}/approve")
    assert r.status_code == 200 and r.json()["operator"] == "local_web_operator"
    assert (await env.client.post(f"{BASE}/plans/{pid}/run")).status_code == 200
    await executor.wait(pid)
    view = (await env.client.get(f"{BASE}/plans/{pid}")).json()
    assert view["status"] == "succeeded" and view["spent_credits"] == 3.0
    assert stubs["calls"] == [("paid", "local_web_operator", f"{pid}:0")]
    # Одноразово: повтор запрещён, подтверждение израсходовано.
    assert (await env.client.post(f"{BASE}/plans/{pid}/run")).status_code == 409
    assert (await env.client.post(f"{BASE}/plans/{pid}/approve")).status_code == 409


async def test_read_plan_runs_without_approval_and_history_lists_it(env, stubs) -> None:
    pid = (await _preview(env, {"action_id": "zz.read"}, text="прочитай")).json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{pid}/run")).status_code == 200
    await executor.wait(pid)
    h = (await env.client.get(f"{BASE}/history", params={"plan_id": pid})).json()
    events = [i["event"] for i in h["items"]]
    assert "plan_created" in events and "step_succeeded" in events
    assert any(i["request_text"] == "прочитай" for i in h["items"])
    assert (await env.client.get(f"{BASE}/plans/nope")).status_code == 404
    assert (await env.client.post(f"{BASE}/plans/nope/run")).status_code == 404


async def test_kill_switch_refuses_run_via_api(env, stubs) -> None:
    pid = (await _preview(env, {"action_id": "zz.read"})).json()["plan_id"]
    await kill_switch.set_state(env.factory, active=True, actor="op", rationale="emergency")
    r = await env.client.post(f"{BASE}/plans/{pid}/run")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "kill_switch_active"
    assert stubs["calls"] == []


async def test_stop_endpoint_stops_a_running_plan(env, stubs) -> None:
    pid = (await _preview(env, {"action_id": "zz.slow"}, {"action_id": "zz.read"})).json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{pid}/run")).status_code == 200
    await asyncio.sleep(0.05)
    r = await env.client.post(f"{BASE}/plans/{pid}/stop")
    assert r.status_code == 200 and r.json()["stop_requested"] is True
    stubs["gate"].set()
    await executor.wait(pid)
    view = (await env.client.get(f"{BASE}/plans/{pid}")).json()
    assert view["status"] == "stopped" and view["steps"][1]["status"] == "skipped"
    assert stubs["calls"] == []


async def test_auth_required_when_enabled_and_identity_is_recorded(env, stubs, monkeypatch) -> None:
    from app.settings import settings
    from app.web.auth_sessions import issue_session_token
    monkeypatch.setattr(settings, "web_auth_user", "boss")
    monkeypatch.setattr(settings, "web_auth_password", "pw")
    for method, path in (("GET", "/catalog"), ("GET", "/history"), ("POST", "/plans/x/run"),
                         ("POST", "/plans/x/approve"), ("POST", "/plans/x/stop"),
                         ("GET", "/plans/x")):
        r = await env.client.request(method, BASE + path)
        assert r.status_code == 401, path
    token = issue_session_token("boss")
    headers = {"Authorization": f"Bearer {token}"}
    r = await env.client.post(f"{BASE}/plans/preview", headers=headers,
                              json={"steps": [{"action_id": "zz.paid"}]})
    pid = r.json()["plan_id"]
    approved = (await env.client.post(f"{BASE}/plans/{pid}/approve", headers=headers)).json()
    assert approved["operator"].startswith("web_operator:")
