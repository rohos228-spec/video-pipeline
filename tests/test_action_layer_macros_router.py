"""GET/POST/DELETE /macros и POST /plans/from-macro через ASGI-клиент."""
from __future__ import annotations

import pytest_asyncio

from app.models import Frame
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env, make_spec  # noqa: F401
from tests.test_action_layer_macros import PARAMS, SCHEMA, good_steps

BASE = "/api/action-layer"


@pytest_asyncio.fixture
async def reg(env):  # noqa: F811
    ensure_builtin_actions()
    ran: list = []

    async def noop(ctx, params):
        ran.append(1)
        return {"credits_spent": 1.0}

    specs = [make_spec("zz.read", noop, schema=SCHEMA),
             make_spec("zz.paid", noop, risk="paid", spends=True, cost=(1, 2), schema=SCHEMA)]
    for s in specs:
        registry.register(s)
    env.ran = ran
    yield env
    for s in specs:
        registry.unregister(s.id)


def body(**kw):
    return {"title_ru": "Моя кнопка", "description_ru": "d", "steps": good_steps(),
            "params_schema": PARAMS, **kw}


async def test_list_contains_builtins_with_availability(reg) -> None:
    r = await reg.client.get(f"{BASE}/macros")
    assert r.status_code == 200
    by_id = {m["id"]: m for m in r.json()["macros"]}
    assert by_id["smart.check_board"]["available"] is True
    assert by_id["smart.check_board"]["builtin"] is True
    for m in by_id.values():
        assert m["available"] == (not m["missing_action_ids"])


async def test_create_list_delete_user_macro(reg) -> None:
    r = await reg.client.post(f"{BASE}/macros", json=body())
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["id"].startswith("user.") and created["mine"] and created["shared"] is False
    listed = (await reg.client.get(f"{BASE}/macros")).json()["macros"]
    assert created["id"] in {m["id"] for m in listed}
    assert (await reg.client.delete(f"{BASE}/macros/{created['id']}")).status_code == 200
    assert (await reg.client.delete(f"{BASE}/macros/{created['id']}")).status_code == 404
    assert (await reg.client.delete(f"{BASE}/macros/smart.check_board")).status_code == 403


async def test_create_rejects_invalid_macro_and_extra_fields(reg) -> None:
    bad = body(steps=[{"action_id": "shell.exec", "params": {}}])
    r = await reg.client.post(f"{BASE}/macros", json=bad)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "macro_invalid"
    assert (await reg.client.post(f"{BASE}/macros", json=body(approved=True))).status_code == 422
    step_extra = body(steps=[{**good_steps()[0], "approval_id": "x"}])
    assert (await reg.client.post(f"{BASE}/macros", json=step_extra)).status_code == 422


async def test_from_macro_builds_preview_only(reg) -> None:
    pid = await reg.add_project()
    mid = (await reg.client.post(f"{BASE}/macros", json=body())).json()["id"]
    r = await reg.client.post(f"{BASE}/plans/from-macro", json={
        "macro_id": mid, "params": {"project_id": pid, "n": 2}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["macro_id"] == mid and out["preview"]["ok"] and out["preview"]["needs_approval"]
    assert out["preview"]["cost"]["high"] == 2.0
    view = (await reg.client.get(f"{BASE}/plans/{out['plan_id']}")).json()
    assert view["status"] == "proposed" and [s["action_id"] for s in view["steps"]] == [
        "zz.read", "zz.paid"]
    assert view["steps"][1]["params"] == {"project_id": pid, "n": 2}
    assert reg.ran == []
    r = await reg.client.post(f"{BASE}/plans/{out['plan_id']}/run")
    assert r.status_code == 403  # платный макрос тоже требует подтверждения оператора


async def test_from_macro_error_cases(reg) -> None:
    pid = await reg.add_project()
    r = await reg.client.post(f"{BASE}/plans/from-macro", json={"macro_id": "user.nope"})
    assert r.status_code == 404
    mid = (await reg.client.post(f"{BASE}/macros", json=body())).json()["id"]
    r = await reg.client.post(f"{BASE}/plans/from-macro", json={
        "macro_id": mid, "params": {"project_id": pid, "bogus": 1}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "macro_params_invalid"
    r = await reg.client.post(f"{BASE}/plans/from-macro", json={
        "macro_id": mid, "params": {"project_id": 99999}})
    assert r.status_code == 404  # проекта нет
    r = await reg.client.post(f"{BASE}/plans/from-macro", json={
        "macro_id": mid, "params": {"project_id": pid}, "approved": True})
    assert r.status_code == 422
    registry.unregister("zz.paid")
    r = await reg.client.post(f"{BASE}/plans/from-macro", json={
        "macro_id": mid, "params": {"project_id": pid}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "macro_unavailable"
    assert r.json()["detail"]["missing_action_ids"] == ["zz.paid"]


async def test_builtin_macros_are_all_available_and_preview_works(reg) -> None:
    pid = await reg.add_project()
    listed = (await reg.client.get(f"{BASE}/macros")).json()["macros"]
    builtin = [m for m in listed if m["builtin"]]
    assert builtin and all(m["available"] for m in builtin)
    r = await reg.client.post(f"{BASE}/plans/from-macro", json={
        "macro_id": "smart.check_board", "params": {"project_id": pid}})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["risk_max"] == "read"


async def test_macro_plan_goes_stale_when_project_changes(reg) -> None:
    pid = await reg.add_project()
    plan_id = (await reg.client.post(f"{BASE}/plans/from-macro", json={
        "macro_id": "smart.check_board", "params": {"project_id": pid}})).json()["plan_id"]
    async with reg.factory() as s:
        s.add(Frame(project_id=pid, number=7, voiceover_text="новый", attrs={}))
        await s.commit()
    r = await reg.client.post(f"{BASE}/plans/{plan_id}/run")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_context"
