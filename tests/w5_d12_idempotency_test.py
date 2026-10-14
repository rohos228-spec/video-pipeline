"""W5-4 D12: идемпотентность записывающих вызовов слоя и проверка атрибуции оператора.

Сервер заменён маленьким приложением со счётчиком: считаем, сколько раз запись реально дошла
до эндпоинта. Сети и денег нет.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, File, Request, UploadFile

from app.services.action_layer import store
from app.services.action_layer.context import (
    ActionCallError, ActionContext, FileUpload, normalize_operator)
from tests.action_layer_support import env  # noqa: F401


def _counting_app(refuse_first: bool = False, boom: bool = False) -> tuple[FastAPI, dict]:
    app = FastAPI()
    state = {"writes": 0, "headers": []}

    @app.post("/api/write")
    async def write(body: dict):
        state["writes"] += 1
        return {"n": state["writes"], "echo": body}

    @app.post("/api/refuse")
    async def refuse(body: dict):
        state["writes"] += 1
        if refuse_first and state["writes"] == 1:
            return {"rejected": "queue_conflict"}
        return {"ok": True, "n": state["writes"]}

    @app.post("/api/fail")
    async def fail(body: dict):
        state["writes"] += 1
        if state["writes"] == 1:
            from fastapi import HTTPException
            raise HTTPException(400, "нельзя")
        return {"ok": True, "n": state["writes"]}

    @app.get("/api/read")
    async def read():
        state["writes"] += 100
        return {"n": state["writes"]}

    return app, state


def _ctx(app, key="step-1", **kw) -> ActionContext:
    return ActionContext(app=app, operator="web_operator:abc", plan_id="plan1", step_index=0,
                         idempotency_key=key, **kw)


async def test_repeat_with_same_key_does_not_duplicate_the_write(env) -> None:
    app, state = _counting_app()
    first = await _ctx(app).call("POST", "/write", json={"a": 1})
    again = await _ctx(app).call("POST", "/write", json={"a": 1})
    assert state["writes"] == 1
    assert again == first and again["n"] == 1


async def test_same_key_with_different_request_is_a_different_call(env) -> None:
    app, state = _counting_app()
    await _ctx(app).call("POST", "/write", json={"a": 1})
    await _ctx(app).call("POST", "/write", json={"a": 2})
    await _ctx(app, key="step-2").call("POST", "/write", json={"a": 1})
    assert state["writes"] == 3


async def test_reads_are_never_deduplicated(env) -> None:
    app, state = _counting_app()
    await _ctx(app).call("GET", "/read")
    await _ctx(app).call("GET", "/read")
    assert state["writes"] == 200


async def test_call_without_key_is_not_recorded(env) -> None:
    app, state = _counting_app()
    ctx = ActionContext(app=app, operator="x")
    await ctx.call("POST", "/write", json={"a": 1})
    await ctx.call("POST", "/write", json={"a": 1})
    assert state["writes"] == 2


async def test_failed_call_may_be_retried(env) -> None:
    app, state = _counting_app()
    with pytest.raises(ActionCallError) as err:
        await _ctx(app).call("POST", "/fail", json={})
    assert err.value.status == 400
    ok = await _ctx(app).call("POST", "/fail", json={})
    assert ok["ok"] is True and state["writes"] == 2


async def test_refusal_inside_a_2xx_answer_is_not_cached(env) -> None:
    app, state = _counting_app(refuse_first=True)
    first = await _ctx(app).call("POST", "/refuse", json={})
    assert first == {"rejected": "queue_conflict"}
    second = await _ctx(app).call("POST", "/refuse", json={})
    assert second["ok"] is True and state["writes"] == 2
    third = await _ctx(app).call("POST", "/refuse", json={})
    assert third == second and state["writes"] == 2


async def test_unfinished_attempt_blocks_a_blind_repeat(env) -> None:
    """Попытка началась и не записала исход (обрыв): результат неизвестен, повтор запрещён."""
    app, state = _counting_app()
    fp = store.call_fingerprint("POST", "/write", params=None, body={"a": 1}, files=None)
    verdict, _ = await store.claim_call("step-1", fp, method="POST", path="/write",
                                        operator="x", plan_id="plan1")
    assert verdict == "new"
    with pytest.raises(ActionCallError) as err:
        await _ctx(app).call("POST", "/write", json={"a": 1})
    assert err.value.status == 409 and "результат неизвестен" in str(err.value.detail)
    assert state["writes"] == 0


async def test_idempotent_action_may_repeat_after_an_unfinished_attempt(env) -> None:
    """Для идемпотентного действия (по реестру) недописанная попытка не блокирует повтор."""
    from app.services.action_layer.spec import ensure_builtin_actions

    ensure_builtin_actions()
    async with env.factory() as s:
        s.add(store.ActionPlanStep(plan_id="planI", idx=0, action_id="project.set_auto_mode",
                                   params={}, idempotency_key="step-I"))
        await s.commit()
    app, state = _counting_app()
    fp = store.call_fingerprint("POST", "/write", params=None, body={"a": 1}, files=None)
    await store.claim_call("step-I", fp, method="POST", path="/write", operator="x",
                           plan_id="planI")
    ctx = ActionContext(app=app, operator="x", plan_id="planI", step_index=0,
                        idempotency_key="step-I")
    out = await ctx.call("POST", "/write", json={"a": 1})
    assert out["n"] == 1 and state["writes"] == 1
    again = await ctx.call("POST", "/write", json={"a": 1})
    assert again == out and state["writes"] == 1


async def test_upload_is_fingerprinted_by_content(env) -> None:
    app2 = FastAPI()
    seen2: list[bytes] = []

    @app2.post("/api/up")
    async def up2(file: UploadFile = File(...)):
        seen2.append(await file.read())
        return {"ok": True, "size": len(seen2[-1])}

    a = FileUpload.build("check_agent", "a.txt", b"one")
    b = FileUpload.build("check_agent", "a.txt", b"two")
    ctx = _ctx(app2)
    await ctx.call("POST", "/up", files={"file": a})
    await ctx.call("POST", "/up", files={"file": a})
    await ctx.call("POST", "/up", files={"file": b})
    assert seen2 == [b"one", b"two"]


async def test_operator_header_is_checked_and_sent(env) -> None:
    app = FastAPI()
    got = {}

    @app.post("/api/who")
    async def who(request: Request):
        got["op"] = request.headers.get("x-action-layer-operator")
        got["key"] = request.headers.get("idempotency-key")
        return {"ok": True}

    await _ctx(app, key="k-9").call("POST", "/who", json={})
    assert got == {"op": "web_operator:abc", "key": "k-9"}
    evil = ActionContext(app=app, operator="web\r\nX-Evil: 1 Оператор", idempotency_key=None)
    await evil.call("POST", "/who", json={})
    assert "\n" not in got["op"] and "\r" not in got["op"] and got["op"].isascii()


def test_normalize_operator_rules() -> None:
    assert normalize_operator("web_operator:ab12") == "web_operator:ab12"
    assert normalize_operator("") == "unknown" and normalize_operator(None) == "unknown"
    assert normalize_operator("a b\nc") == "a_b_c"
    assert len(normalize_operator("x" * 500)) == 120


async def test_new_tables_exist_after_schema_setup(env) -> None:
    async with env.engine.connect() as conn:
        names = await conn.run_sync(lambda c: __import__("sqlalchemy").inspect(c).get_table_names())
    assert "action_layer_calls" in names and "action_layer_snapshots" in names
