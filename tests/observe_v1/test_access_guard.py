"""Доступ к изменяющим ручкам без веб-авторизации: только loopback (без сети)."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from loguru import logger

from app.services import access_guard
from app.services.access_guard import local_only_when_no_auth
from app.settings import settings
from app.web.auth_sessions import require_web_user
from tests.action_layer_support import env  # noqa: F401

REMOTE = ("203.0.113.5", 5555)


def test_is_loopback_host():
    for h in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "testclient", None):
        assert access_guard.is_loopback_host(h), h
    for h in ("192.168.1.5", "203.0.113.5", "10.0.0.1", "garbage"):
        assert not access_guard.is_loopback_host(h), h


def _mini_app() -> FastAPI:
    app = FastAPI()

    @app.post("/m", dependencies=[Depends(local_only_when_no_auth)])
    async def m():
        return {"ok": True}
    return app


async def _post(app, client_addr):
    async with AsyncClient(transport=ASGITransport(app=app, client=client_addr),
                           base_url="http://t") as c:
        return await c.post("/m")


async def test_guard_local_ok_remote_denied():
    app = _mini_app()
    assert (await _post(app, ("127.0.0.1", 1))).status_code == 200
    assert (await _post(app, ("::1", 1))).status_code == 200
    r = await _post(app, REMOTE)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "local_only"


async def test_guard_remote_allowed_by_setting(monkeypatch):
    monkeypatch.setattr(settings, "orchestrator_allow_remote_without_auth", True)
    assert (await _post(_mini_app(), REMOTE)).status_code == 200


async def test_guard_noop_when_web_auth_on(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "u")
    monkeypatch.setattr(settings, "web_auth_password", "p")
    assert (await _post(_mini_app(), REMOTE)).status_code == 200


def _calls(route: APIRoute):
    stack, out = list(route.dependant.dependencies), []
    while stack:
        d = stack.pop()
        out.append(d.call)
        stack.extend(d.dependencies)
    return out


def test_every_mutating_route_is_guarded_and_every_get_is_authed():
    from app.services.action_layer.router import router as al_router
    from app.web.routers.observations import router as obs_router
    seen_post = seen_get = 0
    for router, authed_gets in ((al_router, False), (obs_router, True)):
        for r in router.routes:
            if not isinstance(r, APIRoute):
                continue
            calls = _calls(r)
            if r.methods & {"POST", "DELETE", "PUT", "PATCH"}:
                assert local_only_when_no_auth in calls, (r.methods, r.path)
                seen_post += 1
            elif authed_gets:
                assert require_web_user in calls, (r.methods, r.path)
                seen_get += 1
    assert seen_post >= 20 and seen_get >= 9


async def test_real_routers_deny_remote_client(env):
    async with AsyncClient(transport=ASGITransport(app=env.app, client=REMOTE),
                           base_url="http://t") as c:
        for method, url, body in (
            ("post", "/api/action-layer/plans/preview", {"steps": [{"action_id": "x"}]}),
            ("post", "/api/action-layer/plans/abc/approve", None),
            ("post", "/api/action-layer/plans/abc/run", None),
            ("post", "/api/action-layer/plans/abc/stop", None),
            ("delete", "/api/action-layer/macros/m1", None),
            ("post", "/api/projects/1/observations/kill-switch", {}),
            ("post", "/api/projects/1/observations/e1/commit-uncertainty/clear", {}),
            ("post", "/api/projects/1/observations/launch", {}),
        ):
            r = await getattr(c, method)(url, **({"json": body} if body is not None else {}))
            assert r.status_code == 403, (url, r.status_code, r.text)
            assert r.json()["detail"]["code"] == "local_only"
    r = await env.client.post("/api/action-layer/plans/abc/stop")
    assert r.status_code != 403


async def test_observation_reads_need_auth_when_enabled(env, monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "u")
    monkeypatch.setattr(settings, "web_auth_password", "p")
    for url in ("/api/projects/1/observations", "/api/projects/1/observations/e1/events",
                "/api/projects/1/observations/e1/decisions",
                "/api/projects/1/observations/kill-switch",
                "/api/projects/1/observations/telemetry"):
        r = await env.client.get(url)
        assert r.status_code == 401, (url, r.status_code)


def test_startup_warning_when_exposed_without_auth(monkeypatch):
    msgs: list[str] = []
    hid = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    try:
        monkeypatch.setattr(settings, "web_host", "0.0.0.0")
        assert access_guard.warn_if_exposed_without_auth() is True
        assert any("SECURITY" in m for m in msgs)
        monkeypatch.setattr(settings, "web_host", "127.0.0.1")
        assert access_guard.warn_if_exposed_without_auth() is False
        monkeypatch.setattr(settings, "web_host", "0.0.0.0")
        monkeypatch.setattr(settings, "web_auth_user", "u")
        monkeypatch.setattr(settings, "web_auth_password", "p")
        assert access_guard.warn_if_exposed_without_auth() is False
    finally:
        logger.remove(hid)
