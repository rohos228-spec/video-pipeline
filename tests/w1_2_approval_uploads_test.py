"""W1-2 / A06: подтверждение только с токеном оператора; вложения привязаны к sha256 при запуске."""
from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.raw_client
import pytest_asyncio

from app.services import access_guard as ag
from app.services.action_layer import approval, attachments, service
from app.services.action_layer.context import (
    ActionCallError, ActionContext, FileUpload, UploadRejected, source_to_upload,
    validate_source_params)
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.settings import settings
from tests.action_layer_support import env, make_spec  # noqa: F401

BASE = "/api/action-layer"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VP_OPERATOR_TOKEN_ENFORCE", "1")


@pytest_asyncio.fixture
async def paid_stub():
    ensure_builtin_actions()
    calls: list = []

    async def paid_ex(ctx, params):
        calls.append(ctx.operator)
        return {"credits_spent": 3.0}

    spec = make_spec("zz.w12paid", paid_ex, risk="paid", spends=True, cost=(2, 5))
    registry.register(spec)
    yield calls
    registry.unregister(spec.id)


def _tok() -> dict:
    return {ag.OPERATOR_HEADER: ag.get_operator_token()}


async def _plan(env) -> str:
    r = await env.client.post(f"{BASE}/plans/preview", headers=_tok(),
                              json={"steps": [{"action_id": "zz.w12paid"}], "request_text": "x"})
    assert r.status_code == 200, r.text
    return r.json()["plan_id"]


# --- approve / run ------------------------------------------------------------------------------------

async def test_script_without_token_cannot_create_approve_or_run(env, paid_stub):
    r = await env.client.post(f"{BASE}/plans/preview",
                              json={"steps": [{"action_id": "zz.w12paid"}]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "operator_token_required"
    pid = await _plan(env)
    for tail in ("approve", "run", "stop"):
        r = await env.client.post(f"{BASE}/plans/{pid}/{tail}")
        assert r.status_code == 403, tail
        assert r.json()["detail"]["code"] == "operator_token_required"
        r = await env.client.post(f"{BASE}/plans/{pid}/{tail}", headers={ag.OPERATOR_HEADER: "guess"})
        assert r.status_code == 403
    async with env.factory() as s:
        from sqlalchemy import select

        from app.services.action_layer import store
        assert await s.scalar(select(store.ActionApproval)) is None   # подтверждения не создано
    assert paid_stub == []


async def test_ui_with_token_approves_and_runs_once(env, paid_stub):
    from app.services.action_layer import executor
    pid = await _plan(env)
    r = await env.client.post(f"{BASE}/plans/{pid}/approve", headers=_tok())
    assert r.status_code == 200 and r.json()["operator"] == "local_web_operator"
    assert (await env.client.post(f"{BASE}/plans/{pid}/run")).status_code == 403   # без токена нельзя
    assert (await env.client.post(f"{BASE}/plans/{pid}/run", headers=_tok())).status_code == 200
    await executor.wait(pid)
    assert paid_stub == ["local_web_operator"]


async def test_foreign_page_cannot_run_approve_run_chain(env, paid_stub):
    """Страница с чужого порта делает from-text/preview -> approve -> run: каждый шаг отказ."""
    evil = {"Origin": "http://localhost:9999", **_tok()}   # даже украденный токен не спасает
    for url, body in ((f"{BASE}/plans/preview", {"steps": [{"action_id": "zz.w12paid"}]}),
                      (f"{BASE}/plans/abc/approve", None), (f"{BASE}/plans/abc/run", None)):
        r = await env.client.post(url, headers=evil, **({"json": body} if body else {}))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin", url
    assert paid_stub == []


# --- защита в глубину внутри approval --------------------------------------------------------------------

async def test_http_identity_needs_verified_token_inside_approval_layer(env, paid_stub):
    pid = await _plan(env)
    http_who = approval.OperatorIdentity.from_token(None)
    assert http_who.via_http is True
    with pytest.raises(approval.ApprovalError) as e:
        await service.approve_plan(env.app, pid, http_who)
    assert e.value.code == "operator_token_required"
    marker = ag.operator_token_verified.set(True)
    try:
        out = await service.approve_plan(env.app, pid, http_who)
    finally:
        ag.operator_token_verified.reset(marker)
    assert out["operator"] == "local_web_operator"


async def test_in_code_identity_and_disabled_switch(env, paid_stub, monkeypatch):
    pid = await _plan(env)
    direct = approval.OperatorIdentity("web_operator:abc123")      # не из HTTP
    assert direct.via_http is False
    assert (await service.approve_plan(env.app, pid, direct))["operator"] == "web_operator:abc123"
    pid2 = await _plan(env)
    monkeypatch.setenv("VP_OPERATOR_TOKEN_ENFORCE", "0")
    http_who = approval.OperatorIdentity.from_token("session-token")
    assert (await service.approve_plan(env.app, pid2, http_who))["plan_id"] == pid2


async def test_model_identities_still_refused_even_with_token(env, paid_stub):
    pid = await _plan(env)
    marker = ag.operator_token_verified.set(True)
    try:
        for who in ("planner:x", "model:gpt", "agent:a", "system"):
            with pytest.raises(approval.ApprovalError) as e:
                await service.approve_plan(env.app, pid, approval.OperatorIdentity(who, via_http=True))
            assert e.value.code == "operator_required"
    finally:
        ag.operator_token_verified.reset(marker)


async def test_internal_action_calls_carry_the_operator_token(env):
    """Внутренний ASGI-вызов слоя действий проходит защитный слой (токен подставляется сам)."""
    ctx = ActionContext(app=env.app, operator="local_web_operator")
    with pytest.raises(ActionCallError) as e:
        await ctx.call("POST", "/action-layer/plans/no-such-plan/stop")
    assert e.value.status == 404            # дошёл до обработчика, не 403 operator_token_required


# --- вложения и sha256 ---------------------------------------------------------------------------------------

async def _register(env, rel: str, data: bytes, *, sha: str | None = None) -> None:
    async with env.factory() as s:
        s.add(attachments.ActionAttachment(
            id=hashlib.md5(rel.encode()).hexdigest(), project_id=1, operator="op", name="a.png",
            kind="image", ext=".png", content_type="image/png", size=len(data),
            sha256=sha or hashlib.sha256(data).hexdigest(), rel_path=rel))
        await s.commit()


def _write(rel: str, data: bytes):
    path = settings.data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def test_registered_attachment_loads(env):
    rel = "attachments/1/aaa_pic.png"
    _write(rel, PNG)
    await _register(env, rel, PNG)
    up = source_to_upload("scene_image", {"source_path": rel})
    assert up.content == PNG
    assert validate_source_params({"source_path": rel}, "scene_image") is None


async def test_swapped_file_between_approval_and_run_is_refused(env):
    rel = "attachments/1/bbb_pic.png"
    path = _write(rel, PNG)
    await _register(env, rel, PNG)
    params = {"source_path": rel}
    assert validate_source_params(params, "scene_image") is None       # момент подтверждения
    path.write_bytes(PNG[:-1] + b"\x01")                                # тот же размер, другой файл
    with pytest.raises(UploadRejected, match="sha256"):
        source_to_upload("scene_image", params)                        # момент запуска
    path.write_bytes(PNG + b"extra")                                    # другой размер: видно уже в проверке
    assert "размер" in (validate_source_params(params, "scene_image") or "")
    with pytest.raises(UploadRejected):
        FileUpload.from_data_path("scene_image", rel)


async def test_unregistered_file_in_attachments_dir_is_refused(env):
    rel = "attachments/1/ccc_planted.png"
    _write(rel, PNG)                                                    # подброшен мимо загрузки
    with pytest.raises(UploadRejected, match="реестре"):
        source_to_upload("scene_image", {"source_path": rel})
    assert "реестре" in (validate_source_params({"source_path": rel}, "scene_image") or "")


async def test_wrong_registered_hash_is_refused(env):
    rel = "attachments/1/ddd_pic.png"
    _write(rel, PNG)
    await _register(env, rel, PNG, sha="0" * 64)
    with pytest.raises(UploadRejected, match="sha256"):
        source_to_upload("scene_image", {"source_path": rel})


async def test_other_data_files_are_not_bound(env):
    """Файлы вне attachments/ (результаты проекта) проверяются как раньше, без реестра."""
    rel = "videos/p1/frame_001_x.png"
    _write(rel, PNG)
    assert source_to_upload("scene_image", {"source_path": rel}).content == PNG
