"""Веб-вход Studio: сессии, хэш пароля, ограничение попыток (шаг A05).

* Сессии постоянные: в SQLite-файле ``DATA_DIR/web_sessions.sqlite3`` хранится только sha256 токена
  (сам токен знает браузер), перезапуск backend никого не разлогинивает. Если файл недоступен,
  сессия живёт в памяти процесса, а в лог пишется ошибка (вход не ломается).
* Пароль в ``WEB_AUTH_PASSWORD`` может быть открытым или хэшем
  ``pbkdf2_sha256$<итерации>$<соль>$<хэш>``.
  Сравнение везде через ``hmac.compare_digest``.
* ``LoginThrottle`` — лимит неудачных попыток на адрес и на имя пользователя.
* Зарегистрированные пользователи VIA (``user_accounts``) остаются действительными
  наряду с legacy-токеном из этого модуля.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from loguru import logger

from app.settings import settings

_TOKEN_TTL_SEC = 86400 * 7
_HASH_PREFIX = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 240_000

_MEM: dict[str, tuple[float, str]] = {}
_LOCK = threading.Lock()


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS,
                  salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join((_HASH_PREFIX, str(iterations), base64.b64encode(salt).decode("ascii"),
                     base64.b64encode(digest).decode("ascii")))


def _b(value: str) -> bytes:
    return value.encode("utf-8")


def verify_password(stored: str, supplied: str) -> bool:
    if stored.startswith(_HASH_PREFIX + "$"):
        try:
            _name, iters, salt_b64, hash_b64 = stored.split("$", 3)
            salt, want = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
            got = hashlib.pbkdf2_hmac("sha256", _b(supplied), salt, int(iters))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(got, want)
    return hmac.compare_digest(_b(stored), _b(supplied))


def verify_credentials(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(_b(settings.web_auth_user.strip()), _b(username))
    pwd_ok = verify_password(settings.web_auth_password, password)
    return bool(user_ok & pwd_ok)


class LoginThrottle:

    def __init__(self, max_failures: int = 5, window_s: float = 900.0, lockout_s: float = 900.0,
                 max_keys: int = 4096):
        self.max_failures, self.window_s, self.lockout_s = max_failures, window_s, lockout_s
        self.max_keys = max_keys
        self._fails: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, keys: list[str], now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        with self._lock:
            left = max((self._locked_until.get(k, 0.0) - now for k in keys), default=0.0)
        return int(left) + 1 if left > 0 else 0

    def record_failure(self, keys: list[str], now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._fails) > self.max_keys:
                self._prune(now)
            for k in keys:
                hits = [t for t in self._fails.get(k, []) if now - t <= self.window_s]
                hits.append(now)
                self._fails[k] = hits
                if len(hits) >= self.max_failures:
                    self._locked_until[k] = now + self.lockout_s
                    self._fails[k] = []

    def record_success(self, keys: list[str]) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)
                self._locked_until.pop(k, None)

    def _prune(self, now: float) -> None:
        for k in [k for k, v in self._fails.items() if not v or now - v[-1] > self.window_s]:
            self._fails.pop(k, None)
        for k in [k for k, t in self._locked_until.items() if t <= now]:
            self._locked_until.pop(k, None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()
            self._locked_until.clear()


login_throttle = LoginThrottle()


def _db_path() -> Path:
    return Path(settings.data_dir) / "web_sessions.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS web_sessions ("
                "token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, issued_at REAL NOT NULL)")
    return con


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    key, now = _hash_token(token), time.time()
    with _LOCK:
        _MEM[key] = (now, username)
        try:
            con = _connect()
            try:
                con.execute("DELETE FROM web_sessions WHERE issued_at < ?", (now - _TOKEN_TTL_SEC,))
                con.execute("INSERT OR REPLACE INTO web_sessions VALUES (?, ?, ?)", (key, username, now))
                con.commit()
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            logger.exception("web session store unavailable: session kept in memory only")
    return token


def _lookup(key: str) -> float | None:
    with _LOCK:
        hit = _MEM.get(key)
        if hit is not None:
            return hit[0]
        try:
            con = _connect()
            try:
                row = con.execute("SELECT issued_at, username FROM web_sessions WHERE token_hash = ?",
                                  (key,)).fetchone()
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            logger.exception("web session store unavailable")
            return None
        if row is None:
            return None
        _MEM[key] = (float(row[0]), str(row[1]))
        return float(row[0])


def revoke_session_token(token: str | None) -> None:
    if not token:
        return
    key = _hash_token(token)
    with _LOCK:
        _MEM.pop(key, None)
        try:
            con = _connect()
            try:
                con.execute("DELETE FROM web_sessions WHERE token_hash = ?", (key,))
                con.commit()
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            logger.exception("web session store unavailable")


def forget_memory_sessions() -> None:
    with _LOCK:
        _MEM.clear()


def validate_session_token(token: str | None) -> bool:
    if not settings.web_auth_enabled:
        return True
    if not token:
        return False
    try:
        from app.web import user_accounts
        if user_accounts.user_for_session(token):
            return True
    except Exception:
        pass
    issued = _lookup(_hash_token(token))
    if issued is None:
        return False
    if time.time() - issued > _TOKEN_TTL_SEC:
        revoke_session_token(token)
        return False
    return True


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    return request.cookies.get("vp_session")


async def require_web_user(request: Request) -> str | None:
    if not settings.web_auth_enabled:
        return None
    token = _extract_bearer(request)
    if not validate_session_token(token):
        raise HTTPException(status_code=401, detail="login required")
    return token


AuthDep = Annotated[str | None, Depends(require_web_user)]


if __name__ == "__main__":
    _pw = getpass.getpass("Пароль для входа в Studio: ")
    if _pw != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпали.")
    print("WEB_AUTH_PASSWORD=" + hash_password(_pw))
