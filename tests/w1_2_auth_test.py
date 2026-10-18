from __future__ import annotations

import hmac
import time

import pytest

pytestmark = pytest.mark.raw_client
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.settings import settings
from app.web import auth_sessions as sessions
from app.web.routers import auth as auth_router


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "boss")
    monkeypatch.setattr(settings, "web_auth_password", "s3cret-pass")
    monkeypatch.setattr(auth_router, "FAILED_LOGIN_DELAY_S", 0)
    sessions.login_throttle.reset()
    sessions.forget_memory_sessions()
    yield
    sessions.login_throttle.reset()
    sessions.forget_memory_sessions()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api")
    return app


def _client(peer=("127.0.0.1", 4000)) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app(), client=peer), base_url="http://localhost:8765")


async def _login(c, user="boss", pwd="s3cret-pass"):
    return await c.post("/api/auth/login", json={"username": user, "password": pwd})



def test_credentials_use_compare_digest(monkeypatch):
    calls: list[tuple] = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((type(a), type(b)))
        return real(a, b)
    monkeypatch.setattr(sessions.hmac, "compare_digest", spy)
    assert sessions.verify_credentials("boss", "s3cret-pass") is True
    assert len(calls) == 2 and all(t == (bytes, bytes) for t in calls)
    calls.clear()
    assert sessions.verify_credentials("nobody", "s3cret-pass") is False
    assert len(calls) == 2
    calls.clear()
    assert sessions.verify_credentials("boss", "wrong") is False
    assert sessions.verify_credentials("boss", "") is False
    assert sessions.verify_credentials("", "s3cret-pass") is False


async def test_login_endpoint_goes_through_constant_time_check_and_does_not_leak_which_part_is_wrong(monkeypatch):
    calls: list[int] = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append(1)
        return real(a, b)
    monkeypatch.setattr(sessions.hmac, "compare_digest", spy)
    async with _client() as c:
        bad_user = await _login(c, user="nobody", pwd="s3cret-pass")
        n_bad_user = len(calls)
        calls.clear()
        bad_pwd = await _login(c, pwd="wrong")
        n_bad_pwd = len(calls)
        calls.clear()
        ok = await _login(c)
    assert bad_user.status_code == bad_pwd.status_code == 401
    assert bad_user.json() == bad_pwd.json()
    assert n_bad_user == n_bad_pwd == len(calls) == 4
    assert ok.status_code == 200 and ok.json()["token"]


def test_password_hash_roundtrip_and_formats():
    stored = sessions.hash_password("пароль-123", iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$") and "пароль" not in stored
    assert sessions.verify_password(stored, "пароль-123") is True
    assert sessions.verify_password(stored, "пароль-124") is False
    assert sessions.hash_password("x", iterations=1000) != sessions.hash_password("x", iterations=1000)
    for broken in ("pbkdf2_sha256$abc$AA$BB", "pbkdf2_sha256$1000$!!$!!", "pbkdf2_sha256$"):
        assert sessions.verify_password(broken, "x") is False
    assert sessions.verify_password("plain", "plain") and not sessions.verify_password("plain", "Plain")


async def test_login_with_hashed_password_in_settings(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_password", sessions.hash_password("h-pass", iterations=1000))
    async with _client() as c:
        assert (await _login(c, pwd="h-pass")).status_code == 200
        assert (await _login(c, pwd="s3cret-pass")).status_code == 401



async def test_wrong_password_401_then_lockout_429_with_retry_after():
    async with _client() as c:
        for _ in range(5):
            assert (await _login(c, pwd="bad")).status_code == 401
        r = await _login(c, pwd="bad")
        assert r.status_code == 429 and r.json()["detail"]["code"] == "too_many_attempts"
        assert int(r.headers["retry-after"]) > 0
        assert (await _login(c)).status_code == 429


async def test_lockout_is_per_address_and_per_user():
    async with _client(peer=("203.0.113.7", 1)) as attacker:
        for _ in range(5):
            await _login(attacker, pwd="bad")
        assert (await _login(attacker)).status_code == 429
    async with _client(peer=("203.0.113.8", 1)) as other:
        assert (await _login(other)).status_code == 429
        assert (await _login(other, user="someone")).status_code == 401


async def test_success_resets_failure_counter():
    async with _client() as c:
        for _ in range(4):
            assert (await _login(c, pwd="bad")).status_code == 401
        assert (await _login(c)).status_code == 200
        for _ in range(4):
            assert (await _login(c, pwd="bad")).status_code == 401
        assert (await _login(c)).status_code == 200


def test_throttle_window_and_unlock_with_fake_clock():
    th = sessions.LoginThrottle(max_failures=3, window_s=60, lockout_s=120)
    k = ["ip:1"]
    th.record_failure(k, now=0)
    th.record_failure(k, now=10)
    assert th.retry_after(k, now=11) == 0
    th.record_failure(k, now=20)
    assert 99 <= th.retry_after(k, now=21) <= 121
    assert th.retry_after(k, now=141) == 0
    th2 = sessions.LoginThrottle(max_failures=3, window_s=60, lockout_s=120)
    th2.record_failure(k, now=0)
    th2.record_failure(k, now=10)
    th2.record_failure(k, now=100)
    assert th2.retry_after(k, now=101) == 0


def test_throttle_memory_is_bounded():
    th = sessions.LoginThrottle(max_failures=5, window_s=10, max_keys=50)
    for i in range(500):
        th.record_failure([f"ip:{i}"], now=1000 + i)
    assert len(th._fails) < 80


async def test_login_disabled_when_auth_off(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "")
    monkeypatch.setattr(settings, "web_auth_password", "")
    async with _client() as c:
        r = await _login(c, pwd="anything")
        assert r.status_code == 200 and r.json() == {"ok": True, "token": None, "auth_required": False}



async def test_session_survives_restart_and_is_stored_hashed(tmp_path):
    async with _client() as c:
        token = (await _login(c)).json()["token"]
    assert sessions.validate_session_token(token) is True
    sessions.forget_memory_sessions()
    assert sessions.validate_session_token(token) is True
    assert sessions.validate_session_token(token + "x") is False
    assert sessions.validate_session_token(None) is False
    raw = sessions._db_path().read_bytes()
    assert token.encode() not in raw
    assert sessions._hash_token(token).encode() in raw


async def test_logout_revokes_persistently():
    async with _client() as c:
        token = (await _login(c)).json()["token"]
        r = await c.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
    sessions.forget_memory_sessions()
    assert sessions.validate_session_token(token) is False


def test_expired_session_is_rejected_and_purged(monkeypatch):
    token = sessions.issue_session_token("boss")
    real_time = time.time
    monkeypatch.setattr(sessions.time, "time", lambda: real_time() + sessions._TOKEN_TTL_SEC + 5)
    assert sessions.validate_session_token(token) is False
    monkeypatch.setattr(sessions.time, "time", real_time)
    sessions.forget_memory_sessions()
    assert sessions.validate_session_token(token) is False


def test_unavailable_store_falls_back_to_memory(monkeypatch, tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    monkeypatch.setattr(sessions, "_db_path", lambda: blocker / "sessions.sqlite3")
    token = sessions.issue_session_token("boss")
    assert sessions.validate_session_token(token) is True
    sessions.forget_memory_sessions()
    assert sessions.validate_session_token(token) is False


def test_validation_is_open_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_user", "")
    assert sessions.validate_session_token(None) is True



async def test_login_sets_httponly_strict_session_cookie_and_cookie_authorizes():
    async with _client() as c:
        r = await _login(c)
        assert r.status_code == 200
        cookie = r.headers.get("set-cookie", "")
        assert "vp_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        token = r.json()["token"]
        assert token and f"vp_session={token}" in cookie
        c.cookies.set("vp_session", token)
        from fastapi import Request
        req = Request({"type": "http", "headers": [(b"cookie", f"vp_session={token}".encode())]})
        assert await sessions.require_web_user(req) == token


async def test_logout_revokes_cookie_session():
    async with _client() as c:
        token = (await _login(c)).json()["token"]
        out = await c.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        assert out.status_code == 200
        assert "vp_session=" in out.headers.get("set-cookie", "")
        assert sessions.validate_session_token(token) is False


async def test_failed_login_sets_no_cookie():
    async with _client() as c:
        r = await _login(c, pwd="bad")
        assert r.status_code == 401 and "vp_session" not in r.headers.get("set-cookie", "")
