"""W1-2 / A04: Host, Origin, токен оператора, CORS по списку, local-only без прокси, отказ старта.

Сеть не нужна: ASGI-клиент. Настоящее приложение (create_app) используется только для запросов,
которые защитный слой отклоняет ДО обработчика (до БД и действий).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.raw_client
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.services import access_guard as ag
from app.settings import settings

TOKEN_HEADER = ag.OPERATOR_HEADER
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    # Тесты этого файла проверяют именно включённый токен (поведение по умолчанию).
    monkeypatch.setenv("VP_OPERATOR_TOKEN_ENFORCE", "1")
    monkeypatch.delenv("VP_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("VP_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    monkeypatch.setattr(settings, "web_port", 8765)


def _client(app, base="http://localhost:8765", peer=("127.0.0.1", 5000)) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=peer, raise_app_exceptions=False),
                       base_url=base)


# --- Host / Origin: таблицы ----------------------------------------------------------------------

def test_host_allowed_table():
    for h in ("localhost:8765", "127.0.0.1:8765", "[::1]:8765", "LOCALHOST", "app.localhost:1",
              "192.168.1.5:8765", "testserver", "test", "action-layer.invalid"):
        assert ag.host_allowed(h), h
    for h in ("evil.example.com", "evil.example.com:8765", "rebind.attacker.io:8765",
              "localhost.evil.com", "127.0.0.1.evil.com", "", None):
        assert not ag.host_allowed(h), h


def test_host_extra_from_env(monkeypatch):
    assert not ag.host_allowed("studio.corp.example")
    monkeypatch.setenv("VP_ALLOWED_HOSTS", "studio.corp.example")
    assert ag.host_allowed("studio.corp.example:8765")


def test_origin_allowed_table():
    ok = [("http://localhost:3000", "localhost:3000"), ("http://127.0.0.1:8765", "127.0.0.1:8765"),
          ("http://localhost:8765", "localhost:8765"), ("http://[::1]:8765", "[::1]:8765"),
          ("http://localhost:3000", "localhost:8765")]  # из явного списка (dev-фронт)
    for origin, host in ok:
        assert ag.origin_allowed(origin, host), (origin, host)
    bad = [("http://localhost:9999", "localhost:8765"),      # чужая страница на другом порту
           ("http://evil.example.com", "localhost:8765"),
           ("http://evil.example.com:8765", "evil.example.com:8765"),  # rebinding: Host чужой
           ("null", "localhost:8765"), ("*", "localhost:8765"), ("", "localhost:8765"),
           (None, "localhost:8765"), ("file://x", "localhost:8765")]
    for origin, host in bad:
        assert not ag.origin_allowed(origin, host), (origin, host)


def test_cors_list_has_no_wildcard(monkeypatch):
    assert "*" not in ag.allowed_origins()
    monkeypatch.setenv("VP_ALLOWED_ORIGINS", "*, http://studio.lan:8765, мусор")
    got = ag.allowed_origins()
    assert "*" not in got and "http://studio.lan:8765" in got and "мусор" not in got


# --- middleware на мини-приложении -----------------------------------------------------------------

def _mini() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ag.SecurityGuardMiddleware)

    @app.post("/api/plain")
    async def plain():
        return {"ok": True}

    @app.get("/api/read")
    async def read():
        return {"ok": True}

    @app.post("/api/action-layer/plans/{pid}/approve")
    async def approve(pid: str):
        return {"verified": ag.operator_token_verified.get()}

    @app.post("/api/projects/{pid}/observations/kill-switch")
    async def kill(pid: str):
        return {"ok": True}
    return app


async def test_good_requests_pass():
    async with _client(_mini()) as c:
        h = {"Origin": "http://localhost:8765"}
        assert (await c.post("/api/plain", headers=h)).status_code == 200
        assert (await c.post("/api/plain")).status_code == 200          # без Origin (скрипт/curl)
        assert (await c.get("/api/read", headers={"Origin": "http://evil.example.com"})).status_code == 200


async def test_bad_host_denied_for_every_method():
    async with _client(_mini(), base="http://evil.example.com:8765") as c:
        for r in (await c.post("/api/plain"), await c.get("/api/read")):
            assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_host"


async def test_bad_origin_denied_on_mutation_only():
    async with _client(_mini()) as c:
        for origin in ("http://evil.example.com", "http://localhost:9999", "null"):
            r = await c.post("/api/plain", headers={"Origin": origin})
            assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin", origin
        r = await c.post("/api/plain", headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin"
        ok = await c.post("/api/plain", headers={"Sec-Fetch-Site": "same-origin"})
        assert ok.status_code == 200


async def test_operator_token_required_on_sensitive_paths():
    tok = ag.get_operator_token()
    async with _client(_mini()) as c:
        for url in ("/api/action-layer/plans/p1/approve", "/api/projects/7/observations/kill-switch"):
            r = await c.post(url)
            assert r.status_code == 403 and r.json()["detail"]["code"] == "operator_token_required"
            r = await c.post(url, headers={TOKEN_HEADER: "wrong"})
            assert r.status_code == 403
            r = await c.post(url, headers={TOKEN_HEADER: tok})
            assert r.status_code == 200
        # Токен виден обработчику; обычная ручка токена не требует.
        r = await c.post("/api/action-layer/plans/p1/approve", headers={TOKEN_HEADER: tok})
        assert r.json() == {"verified": True}
        assert (await c.post("/api/plain")).status_code == 200


async def test_token_check_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv("VP_OPERATOR_TOKEN_ENFORCE", "0")
    async with _client(_mini()) as c:
        assert (await c.post("/api/action-layer/plans/p1/approve")).status_code == 200


async def test_token_rotation_invalidates_old_token():
    old = ag.get_operator_token()
    new = ag.rotate_operator_token()
    assert old != new
    async with _client(_mini()) as c:
        url = "/api/action-layer/plans/p1/approve"
        assert (await c.post(url, headers={TOKEN_HEADER: old})).status_code == 403
        assert (await c.post(url, headers={TOKEN_HEADER: new})).status_code == 200


def test_sensitive_path_table():
    need = [("POST", "/api/action-layer/plans/abc/approve"), ("POST", "/api/action-layer/plans/abc/run"),
            ("POST", "/api/action-layer/plans/abc/stop"), ("POST", "/api/action-layer/plans/from-text"),
            ("POST", "/api/projects/3/observations/kill-switch"),
            ("POST", "/api/projects/3/media-recovery/release-unknown"),
            ("POST", "/api/projects/3/steps/make_plan/run"), ("POST", "/api/projects/3/stop"),
            ("POST", "/api/projects/3/publish"), ("POST", "/api/projects/3/social/publish-now"),
            ("POST", "/api/projects/3/publications/youtube/approve"),
            ("POST", "/api/projects/3/publications/youtube/confirm-manual"),
            ("POST", "/api/projects/3/publications/youtube/mark-failed"),
            ("DELETE", "/api/action-layer/macros/m1"), ("POST", "/api/runs/9/cancel"),
            ("POST", "/api/change-control/anything")]
    for method, path in need:
        assert ag.path_needs_operator_token(path, method), (method, path)
    for method, path in [("GET", "/api/action-layer/plans/abc"), ("GET", "/api/projects/3/stop"), ("GET", "/api/projects/3/publications"),
                         ("POST", "/api/auth/login"), ("POST", "/api/prompts")]:
        assert not ag.path_needs_operator_token(path, method), (method, path)


# --- настоящее приложение: перебор мутирующих ручек ---------------------------------------------------

def _sample(path: str) -> str:
    import re
    return re.sub(r"\{[^}]+\}", "1", path)


def _mutating_ops(app) -> list[tuple[str, str]]:
    """(метод, путь) всех изменяющих ручек /api по схеме OpenAPI (устойчиво к версии FastAPI)."""
    out = []
    for path, item in app.openapi()["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method in item:
            if method.upper() in MUTATING:
                out.append((method.upper(), _sample(path)))
    return sorted(set(out))


def _real_app():
    from app.web.api import create_app
    return create_app()


async def test_every_mutating_route_denies_foreign_host_and_origin():
    app = _real_app()
    ops = _mutating_ops(app)
    assert len(ops) > 150  # перебор действительно широкий
    seen_sensitive = 0
    async with _client(app) as c:
        for method, url in ops:
            r = await c.request(method, url, headers={"Host": "evil.example.com"})
            assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_host", (method, url)
            r = await c.request(method, url, headers={"Origin": "http://evil.example.com"})
            assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin", (method, url)
            r = await c.request(method, url, headers={"Origin": "http://localhost:9999"})
            assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin", (method, url)
            if ag.path_needs_operator_token(url, method):
                seen_sensitive += 1
                r = await c.request(method, url)  # свой хост, без токена
                assert r.status_code == 403 and (
                    r.json()["detail"]["code"] == "operator_token_required"), (method, url)
    assert seen_sensitive >= 15


async def test_sensitive_real_routes_are_covered():
    """Опасные ручки, названные в плане, реально существуют и требуют токен."""
    ops = set(_mutating_ops(_real_app()))
    must = [("POST", "/api/action-layer/plans/1/approve"), ("POST", "/api/action-layer/plans/1/run"),
            ("POST", "/api/action-layer/plans/1/stop"),
            ("POST", "/api/projects/1/observations/kill-switch"),
            ("POST", "/api/projects/1/media-recovery/release-unknown"),
            ("POST", "/api/projects/1/stop")]
    for method, path in must:
        assert (method, path) in ops, (method, path)
        assert ag.path_needs_operator_token(path, method), (method, path)


async def test_websocket_foreign_host_closed():
    app = _real_app()
    sent: list[dict] = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "websocket", "path": "/ws/any", "headers": [(b"host", b"evil.example.com")],
             "query_string": b"", "client": ("127.0.0.1", 1), "server": ("localhost", 8765)}
    await app(scope, receive, send)
    assert sent and sent[0]["type"] == "websocket.close" and sent[0]["code"] == 1008
    sent.clear()
    scope["headers"] = [(b"host", b"localhost:8765"), (b"origin", b"http://evil.example.com")]
    await app(scope, receive, send)
    assert sent and sent[0]["type"] == "websocket.close"


# --- выдача токена -------------------------------------------------------------------------------------

async def test_operator_token_endpoint_only_for_own_origin():
    app = _real_app()
    async with _client(app) as c:
        r = await c.get("/api/auth/operator-token")
        assert r.status_code == 200 and r.json()["token"] == ag.get_operator_token()
        assert r.json()["header"] == TOKEN_HEADER and r.json()["required"] is True
        r = await c.get("/api/auth/operator-token", headers={"Origin": "http://localhost:8765"})
        assert r.status_code == 200
        r = await c.get("/api/auth/operator-token", headers={"Origin": "http://evil.example.com"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin"
        r = await c.get("/api/auth/operator-token", headers={"Origin": "http://localhost:9999"})
        assert r.status_code == 403
        r = await c.get("/api/auth/operator-token", headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403
    async with _client(app, base="http://evil.example.com:8765") as c:   # DNS-rebinding
        r = await c.get("/api/auth/operator-token")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_host"
    async with _client(app, peer=("203.0.113.5", 4000)) as c:            # чужой адрес без входа
        r = await c.get("/api/auth/operator-token")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "local_only"


# --- CORS ------------------------------------------------------------------------------------------------

async def test_cors_preflight_uses_explicit_list():
    app = _real_app()
    async with _client(app) as c:
        pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": TOKEN_HEADER}
        r = await c.options("/api/action-layer/plans/preview", headers={**pre, "Origin": "http://evil.example.com"})
        assert r.headers.get("access-control-allow-origin") is None
        r = await c.options("/api/action-layer/plans/preview", headers={**pre, "Origin": "http://localhost:3000"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert TOKEN_HEADER.lower() in r.headers.get("access-control-allow-headers", "").lower()
        r = await c.get("/api/auth/status", headers={"Origin": "http://evil.example.com"})
        assert r.headers.get("access-control-allow-origin") not in ("*", "http://evil.example.com")


# --- local-only: прокси не считается локальным ---------------------------------------------------------

def _local_app() -> FastAPI:
    app = FastAPI()

    @app.post("/m", dependencies=[ag.MutationLocalOnlyDep])
    async def m():
        return {"ok": True}

    @app.get("/m", dependencies=[ag.MutationLocalOnlyDep])
    async def g():
        return {"ok": True}

    @app.post("/strict")
    async def strict(request: Request, _=Depends(ag.local_only_when_no_auth)):
        return {"ok": True}
    return app


async def test_reverse_proxy_on_loopback_is_not_local():
    async with _client(_local_app()) as c:
        assert (await c.post("/m")).status_code == 200
        for hdr in ({"X-Forwarded-For": "203.0.113.9"}, {"Forwarded": "for=203.0.113.9"},
                    {"X-Real-IP": "203.0.113.9"}):
            r = await c.post("/m", headers=hdr)
            assert r.status_code == 403 and r.json()["detail"]["code"] == "local_only", hdr
            assert (await c.post("/strict", headers=hdr)).status_code == 403
        # Чтение через прокси не режем (зависимость только на мутации).
        assert (await c.get("/m", headers={"X-Forwarded-For": "203.0.113.9"})).status_code == 200


async def test_remote_allowed_when_auth_enabled_even_proxied(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "u")
    monkeypatch.setattr(settings, "web_auth_password", "p")
    async with _client(_local_app(), peer=("203.0.113.5", 1)) as c:
        assert (await c.post("/m", headers={"X-Forwarded-For": "1.2.3.4"})).status_code == 200


def test_studio_routers_are_wrapped_in_local_only_for_mutations():
    """Роутеры из плана закрыты зависимостью на уровне include_router (структурная проверка:
    настоящие обработчики не запускаем, чтобы не трогать данные)."""
    from app.web.routers import (
        create_queue, gen_assistant, gpt_workspace, kie_create, outsee_create, outsee_http,
        project_ops, projects, text_llm, workflows)
    must = {id(m.router): m.router.prefix for m in (workflows, projects, project_ops, outsee_create, outsee_http,
                               kie_create, gpt_workspace, create_queue, gen_assistant, text_llm)}
    guarded = set()
    for route in _real_app().routes:
        ctx = getattr(route, "include_context", None)
        if ctx is None:
            continue
        calls = [d.dependency for d in ctx.dependencies]
        if ag.local_only_for_mutations in calls:
            guarded.add(id(route.original_router))
    assert set(must) <= guarded, [must[i] for i in set(must) - guarded]


async def test_mutation_guard_denies_remote_but_allows_reads():
    app = FastAPI()

    @app.post("/w", dependencies=[ag.MutationLocalOnlyDep])
    async def w():
        return {"ok": True}

    @app.get("/w", dependencies=[ag.MutationLocalOnlyDep])
    async def r():
        return {"ok": True}
    async with _client(app, peer=("203.0.113.5", 1)) as c:
        assert (await c.get("/w")).status_code == 200
        resp = await c.post("/w")
        assert resp.status_code == 403 and resp.json()["detail"]["code"] == "local_only"


# --- отказ старта ---------------------------------------------------------------------------------------

def test_assert_safe_bind(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "")
    monkeypatch.setattr(settings, "web_auth_password", "")
    monkeypatch.setattr(settings, "orchestrator_allow_remote_without_auth", False)
    for host in ("127.0.0.1", "localhost", "::1", ""):
        monkeypatch.setattr(settings, "web_host", host)
        ag.assert_safe_bind()
    for host in ("0.0.0.0", "192.168.1.10", "::", "studio.lan"):
        monkeypatch.setattr(settings, "web_host", host)
        with pytest.raises(ag.UnsafeBindError) as e:
            ag.assert_safe_bind()
        assert "Отказ старта" in str(e.value)
    monkeypatch.setattr(settings, "web_host", "0.0.0.0")
    monkeypatch.setattr(settings, "web_auth_user", "u")
    monkeypatch.setattr(settings, "web_auth_password", "p")
    ag.assert_safe_bind()                                   # вход включён
    monkeypatch.setattr(settings, "web_auth_user", "")
    monkeypatch.setattr(settings, "orchestrator_allow_remote_without_auth", True)
    ag.assert_safe_bind()                                   # явное исключение владельца (с предупреждением)


async def test_lifespan_refuses_to_start_when_exposed_without_auth(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "")
    monkeypatch.setattr(settings, "web_auth_password", "")
    monkeypatch.setattr(settings, "orchestrator_allow_remote_without_auth", False)
    monkeypatch.setattr(settings, "web_host", "0.0.0.0")
    app = _real_app()
    with pytest.raises(ag.UnsafeBindError):
        async with app.router.lifespan_context(app):
            pytest.fail("сервер не должен был стартовать")
