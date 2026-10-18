"""VIA user accounts: registration, sessions, email verification, password reset (SQLite, data/users.db).

Based on main's auth (app/web/auth_sessions.py + routers/auth.py: opaque bearer token / vp_session cookie,
7-day TTL, enforcement only when WEB_AUTH_USER/WEB_AUTH_PASSWORD are set). Adds persistent users.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import sqlite3
import time
from email.message import EmailMessage
from pathlib import Path

from app.settings import settings

log = logging.getLogger("via.auth")

SESSION_TTL = 86400 * 7          # as in main
RESET_TTL = 3600                 # 1 h
VERIFY_TTL = 86400               # 24 h
_DB = Path(settings.data_dir) / "users.db"
_PBKDF_ITER = 260_000


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT UNIQUE NOT NULL, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
          email_verified INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, last_login REAL, plan TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
          created_at REAL NOT NULL, expires_at REAL NOT NULL, user_agent TEXT);
        CREATE TABLE IF NOT EXISTS tokens(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
          kind TEXT NOT NULL, expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        """
    )
    return c


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _PBKDF_ITER)
    return f"pbkdf2_sha256${_PBKDF_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except Exception:
        return False


def public(u: sqlite3.Row | dict) -> dict:
    u = dict(u)
    return {k: u[k] for k in ("id", "email", "username", "name", "role", "created_at", "last_login", "plan")} | {
        "email_verified": bool(u["email_verified"])}


def get_by_login(login: str):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE lower(email)=lower(?) OR lower(username)=lower(?)",
                         (login, login)).fetchone()


def create_user(email: str, username: str, name: str, password: str, plan: str | None = None) -> dict:
    with _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (email,)).fetchone():
            raise ValueError("email_taken")
        if c.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)).fetchone():
            raise ValueError("username_taken")
        first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        cur = c.execute(
            "INSERT INTO users(email,username,name,password_hash,role,created_at,plan) VALUES(?,?,?,?,?,?,?)",
            (email, username, name, hash_password(password), "admin" if first else "user", time.time(), plan))
        return public(c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())


def new_session(user_id: int, ua: str = "") -> str:
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with _conn() as c:
        c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (_h(tok), user_id, now, now + SESSION_TTL, ua[:200]))
        c.execute("UPDATE users SET last_login=? WHERE id=?", (now, user_id))
    return tok


def user_for_session(tok: str | None):
    if not tok:
        return None
    with _conn() as c:
        r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
                      (_h(tok), time.time())).fetchone()
    return r


def drop_session(tok: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (_h(tok),))


def issue_token(user_id: int, kind: str) -> str:
    """kind: reset (url token) | verify (6-digit code)."""
    tok = f"{secrets.randbelow(10**6):06d}" if kind == "verify" else secrets.token_urlsafe(32)
    ttl = VERIFY_TTL if kind == "verify" else RESET_TTL
    with _conn() as c:
        c.execute("UPDATE tokens SET used=1 WHERE user_id=? AND kind=?", (user_id, kind))
        c.execute("INSERT OR REPLACE INTO tokens VALUES(?,?,?,?,0)", (_h(f"{kind}:{user_id}:{tok}" if kind == "verify" else tok), user_id, kind, time.time() + ttl))
    return tok


def consume_reset(tok: str):
    with _conn() as c:
        r = c.execute("SELECT * FROM tokens WHERE token_hash=? AND kind='reset' AND used=0 AND expires_at>?",
                      (_h(tok), time.time())).fetchone()
        if r:
            c.execute("UPDATE tokens SET used=1 WHERE token_hash=?", (_h(tok),))
        return r


def check_reset(tok: str) -> bool:
    with _conn() as c:
        return bool(c.execute("SELECT 1 FROM tokens WHERE token_hash=? AND kind='reset' AND used=0 AND expires_at>?",
                              (_h(tok), time.time())).fetchone())


def consume_verify(user_id: int, code: str) -> bool:
    key = _h(f"verify:{user_id}:{code}")
    with _conn() as c:
        r = c.execute("SELECT 1 FROM tokens WHERE token_hash=? AND used=0 AND expires_at>?", (key, time.time())).fetchone()
        if not r:
            return False
        c.execute("UPDATE tokens SET used=1 WHERE token_hash=?", (key,))
        c.execute("UPDATE users SET email_verified=1 WHERE id=?", (user_id,))
        return True


def set_password(user_id: int, pw: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), user_id))
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def delete_user(user_id: int) -> None:
    with _conn() as c:
        for t in ("sessions", "tokens"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))


def mail_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def send_mail(to: str, subject: str, text: str) -> bool:
    """Send via SMTP_* env if configured. Never log or print the message body."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        log.warning("SMTP_HOST is unset; mail to=%s was not sent", to)
        return False
    try:
        msg = EmailMessage()
        msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "no-reply@via.local"))
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(text)
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("SMTP failed: %s; mail to=%s was not sent", e, to)
        return False
