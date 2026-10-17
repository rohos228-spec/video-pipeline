from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from app.services import access_guard as ag
from app.settings import settings
from app.web import auth_sessions as sessions
from app.web.api import create_app

pytestmark = pytest.mark.raw_client

PUBLIC: dict[tuple[str, str], str] = {
    ("GET", "/api/health"): "проверка живости для скриптов запуска, данных не отдаёт",
    ("GET", "/api/studio-version"): "метка версии UI для страницы входа, данных проекта нет",
    ("POST", "/api/auth/login"): "сама ручка входа",
    ("POST", "/api/auth/logout"): "выход: без сессии ничего не делает",
    ("GET", "/api/auth/status"): "странице нужно узнать, требуется ли вход",
    ("POST", "/api/auth/register"): "регистрация VIA: аккаунта ещё нет, сессии нет",
    ("POST", "/api/auth/forgot"): "запрос сброса пароля, одинаковый ответ без сессии",
    ("GET", "/api/auth/reset/check"): "проверка токена сброса из письма, сессии нет",
    ("POST", "/api/auth/reset"): "смена пароля по токену из письма, сессии нет",
    ("GET", "/api/openapi.json"): "схема FastAPI без данных (модель поставки: loopback)",
    ("GET", "/api/docs"): "Swagger UI, статичная страница без данных",
    ("*", "/api/{rest:path}"): "ловушка неизвестных POST/PUT/PATCH/DELETE: всегда 404, ничего не делает",
}


def _walk(app):
    out: list[tuple[str, str]] = []
    for r in app.routes:
        if hasattr(r, "effective_route_contexts"):
            items = [(c.path, c.methods) for c in r.effective_route_contexts()]
        else:
            items = [(getattr(r, "path", ""), getattr(r, "methods", None))]
        for path, methods in items:
            for m in sorted(methods or ()):
                if m not in ("HEAD", "OPTIONS"):
                    out.append((m, path))
    return out


def _is_public(method: str, path: str) -> bool:
    return (method, path) in PUBLIC or ("*", path) in PUBLIC


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "boss")
    monkeypatch.setattr(settings, "web_auth_password", "s3cret-pass")
    sessions.login_throttle.reset()
    yield
    sessions.forget_memory_sessions()


@pytest.fixture
def client(auth_on):
    return TestClient(create_app(), raise_server_exceptions=False)


def _api_routes(app):
    return [(m, p) for m, p in _walk(app) if p.startswith("/api")]


def test_route_enumeration_is_not_empty(client):
    routes = _api_routes(client.app)
    assert len(routes) > 250, "обход маршрутов сломан (ленивые include_router?)"
    assert any(p == "/api/projects/{project_id}/steps/{step_code}/run" for _, p in routes)


def test_every_api_route_requires_login_when_web_auth_on(client):
    open_routes = []
    for method, path in _api_routes(client.app):
        if _is_public(method, path):
            continue
        r = client.request(method, _concrete(path))
        if r.status_code not in (401, 403):
            open_routes.append(f"{method} {path} -> {r.status_code}")
    assert not open_routes, "без входа доступны:\n" + "\n".join(open_routes)


def test_whitelist_entries_exist_and_are_few(client):
    existing = set(_api_routes(client.app))
    for (method, path) in PUBLIC:
        if method == "*":
            assert any(p == path for _, p in existing), path
        else:
            assert (method, path) in existing, f"белый список устарел: {method} {path}"
    assert len(PUBLIC) <= 12


def test_login_flow_opens_a_route(client):
    assert client.get("/api/workflows").status_code == 401
    token = sessions.issue_session_token("boss")
    r = client.get("/api/workflows", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code not in (401, 403), r.text
    r2 = client.get("/api/workflows", cookies={"vp_session": token})
    assert r2.status_code not in (401, 403)


def test_bad_token_is_rejected(client):
    r = client.get("/api/projects", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


PAID_POSTS = [
    "/api/projects/{project_id}/steps/{step_code}/run",
    "/api/projects/{project_id}/stop",
    "/api/projects/{project_id}/mass-lanes/start",
    "/api/projects/{project_id}/finish/images",
    "/api/projects/{project_id}/finish/videos",
    "/api/projects/{project_id}/finish/animation-prompts",
    "/api/projects/{project_id}/montage-board/queue",
    "/api/projects/{project_id}/montage-board/frames/{frame_id}/scene-action-generate",
    "/api/projects/{project_id}/montage-board/frames/{frame_id}/scene-generate-with-images",
    "/api/projects/{project_id}/observations/launch",
    "/api/projects/{project_id}/observations/{execution_id}/actions/start",
    "/api/projects/{project_id}/observations/{execution_id}/actions/retry",
    "/api/projects/{project_id}/observations/kill-switch",
    "/api/outsee/generate",
    "/api/kie-create/generate",
    "/api/sidebar-layout/gen-queue/enqueue",
    "/api/sidebar-layout/gen-queue/bulk-enqueue",
    "/api/runs/from-workflow/{workflow_id}",
    "/api/action-layer/plans/{plan_id}/approve",
    "/api/action-layer/plans/{plan_id}/run",
    "/api/action-layer/plans/{plan_id}/stop",
    "/api/action-layer/plans/preview",
    "/api/projects/{project_id}/publications/approve",
    "/api/fleet/local/powershell",
    "/api/db/projects/{project_id}/orchestrator/chat",
]


def test_paid_posts_exist_and_need_operator_token(client):
    posts = {p for m, p in _api_routes(client.app) if m == "POST"}
    missing = [p for p in PAID_POSTS if p not in posts]
    assert not missing, f"маршруты переименованы, обновите список: {missing}"
    session = {"Authorization": f"Bearer {sessions.issue_session_token('boss')}"}
    bad = []
    for p in PAID_POSTS:
        r = client.post(_concrete(p), headers=session)
        ctype = r.headers.get("content-type", "")
        body = r.json() if ctype.startswith("application/json") else {}
        detail = body.get("detail") if isinstance(body, dict) else None
        code = detail.get("code") if isinstance(detail, dict) else None
        if r.status_code != 403 or code != "operator_token_required":
            bad.append(f"{p} -> {r.status_code} {code}")
    assert not bad, "платные POST без токена оператора проходят:\n" + "\n".join(bad)


def test_paid_post_with_token_passes_the_guard(client):
    hdr = {"Authorization": f"Bearer {sessions.issue_session_token('boss')}",
           ag.OPERATOR_HEADER: ag.get_operator_token()}
    r = client.post("/api/outsee/generate", headers=hdr, json={})
    assert r.status_code not in (401, 403)


def test_web_auth_off_keeps_loopback_default(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "")
    monkeypatch.setattr(settings, "web_auth_password", "")
    c = TestClient(create_app(), raise_server_exceptions=False)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/workflows").status_code not in (401, 403)
    r = c.post("/api/projects/1/mass-lanes/parse-topics",
               headers={ag.OPERATOR_HEADER: ag.get_operator_token()})
    assert r.status_code not in (401, 403)
    remote = TestClient(create_app(), client=("203.0.113.9", 5000), raise_server_exceptions=False)
    rr = remote.post("/api/projects/1/mass-lanes/parse-topics",
                     headers={ag.OPERATOR_HEADER: ag.get_operator_token()})
    assert rr.status_code == 403
