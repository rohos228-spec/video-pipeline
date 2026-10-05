"""POST /plans/from-text через ASGI-клиент. Транспорт модели - заглушка."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import Frame
from app.services.action_layer import planner, store
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.settings import settings
from tests.action_layer_support import env, make_spec  # noqa: F401
from tests.test_action_layer_planner import FakeTransport, reply, res, step

BASE = "/api/action-layer"


@pytest_asyncio.fixture
async def ctx(env, monkeypatch):  # noqa: F811
    ensure_builtin_actions()
    ran: list = []

    async def read_ex(c, params):
        ran.append("read")
        return {}

    async def paid_ex(c, params):
        ran.append("paid")
        return {"credits_spent": 1.0}

    schema = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1}},
              "required": ["project_id"], "additionalProperties": False}
    specs = [make_spec("zz.read", read_ex, schema=schema),
             make_spec("zz.paid", paid_ex, risk="paid", spends=True, cost=(1, 2), schema=schema)]
    for s in specs:
        registry.register(s)
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test-key")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibecode.moe/v1")
    monkeypatch.setattr(settings, "action_layer_planner_max_catalog", 500)  # без предфильтра
    env.ran = ran
    yield env
    planner.set_transport(None)
    for s in specs:
        registry.unregister(s.id)


def use(*replies) -> FakeTransport:
    fake = FakeTransport(*replies)
    planner.set_transport(fake)
    return fake


async def post(env, text="сделай", **extra):
    return await env.client.post(f"{BASE}/plans/from-text", json={"text": text, **extra})


async def events(env):
    async with env.factory() as s:
        return [r.event for r in (await s.scalars(select(store.ActionHistory))).all()]


async def test_from_text_returns_preview_only(ctx) -> None:
    pid = await ctx.add_project()
    use(res(reply([step("zz.read", {"project_id": pid})], expl="Читаю доску.")))
    r = await post(ctx, "прочитай", project_id=pid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_id"] and body["preview"]["ok"] and body["explanation_ru"] == "Читаю доску."
    assert body["planner"]["prompt_tokens"] == 1000 and body["context_version"]
    view = (await ctx.client.get(f"{BASE}/plans/{body['plan_id']}")).json()
    assert view["status"] == "proposed" and view["approvals"] == [] and ctx.ran == []
    ev = await events(ctx)
    assert {"planner_call", "plan_created", "planner_plan_linked"} <= set(ev)
    assert "approval_created" not in ev and "run_requested" not in ev


async def test_preview_shape_matches_plans_preview(ctx) -> None:
    pid = await ctx.add_project()
    steps = [step("zz.paid", {"project_id": pid})]
    use(res(reply(steps)))
    a = (await post(ctx, project_id=pid)).json()["preview"]
    b = (await ctx.client.post(f"{BASE}/plans/preview", json={"steps": steps})).json()["preview"]
    assert set(a) == set(b) and a["plan_hash"] == b["plan_hash"]
    assert a["needs_approval"] and a["cost"]["high"] == 2.0


async def test_paid_plan_from_text_still_needs_operator_approval(ctx) -> None:
    pid = await ctx.add_project()
    use(res(reply([step("zz.paid", {"project_id": pid})])))
    plan_id = (await post(ctx, project_id=pid)).json()["plan_id"]
    r = await ctx.client.post(f"{BASE}/plans/{plan_id}/run")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "approval_required"
    assert ctx.ran == []


async def test_clarification_creates_no_plan(ctx) -> None:
    use(res(reply([], clar="Какой кадр править?")))
    body = (await post(ctx, "поправь")).json()
    assert body["plan_id"] is None and body["preview"] is None
    assert body["needs_clarification"] == "Какой кадр править?"
    assert "plan_created" not in await events(ctx)


async def test_rejected_plan_is_422_and_not_stored(ctx) -> None:
    use(res(reply([step("shell.exec", {})])))
    r = await post(ctx, "удали всё")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "planner_rejected"
    assert r.json()["detail"]["reason"] == "unknown_action"
    assert "plan_created" not in await events(ctx)


async def test_invalid_precondition_returns_plan_invalid(ctx) -> None:
    use(res(reply([step("montage.get_board", {"project_id": 99999})])))
    r = await post(ctx, "прочитай доску")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "plan_invalid"
    assert "preview" in r.json()["detail"]


async def test_refused_without_key_is_503_and_no_request(ctx, monkeypatch) -> None:
    monkeypatch.setattr(settings, "vibecode_api_key", "")
    fake = use(res(reply([step()])))
    r = await post(ctx)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "key_missing"
    assert fake.calls == []


async def test_transport_failure_is_502(ctx) -> None:
    use(OSError("down"))
    r = await post(ctx)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "transport_error"


async def test_request_cannot_carry_approval_fields(ctx) -> None:
    fake = use(res(reply([step()])))
    for extra in ({"approved": True}, {"approval_id": "x"}, {"run": True}, {"steps": []}):
        r = await post(ctx, **extra)
        assert r.status_code == 422
    assert (await ctx.client.post(f"{BASE}/plans/from-text", json={"text": ""})).status_code == 422
    assert fake.calls == []


async def test_stale_plan_is_refused_on_approve_and_run(ctx) -> None:
    pid = await ctx.add_project()
    use(res(reply([step("zz.paid", {"project_id": pid})])),
        res(reply([step("zz.paid", {"project_id": pid})])))
    fresh = (await post(ctx, project_id=pid)).json()["plan_id"]
    stale = (await post(ctx, project_id=pid)).json()["plan_id"]
    assert (await ctx.client.post(f"{BASE}/plans/{fresh}/approve")).status_code == 200
    async with ctx.factory() as s:
        s.add(Frame(project_id=pid, number=9, voiceover_text="новый", attrs={}))
        await s.commit()
    for path in ("approve", "run"):
        r = await ctx.client.post(f"{BASE}/plans/{stale}/{path}")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_context"
    r = await ctx.client.post(f"{BASE}/plans/{fresh}/run")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_context"
    assert ctx.ran == []


async def test_rate_limit_is_429(ctx, monkeypatch) -> None:
    monkeypatch.setattr(settings, "action_layer_planner_calls_per_hour", 1)
    fake = use(res(reply([step()])), res(reply([step()])))
    assert (await post(ctx, "a")).status_code in (200, 422)  # вызов модели состоялся
    r = await post(ctx, "b")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
    assert len(fake.calls) == 1
