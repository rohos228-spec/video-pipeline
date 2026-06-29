"""Простая auth-сессия для fleet / удалённого доступа."""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.settings import settings

_TOKENS: dict[str, float] = {}
_TOKEN_TTL_SEC = 86400 * 7


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    if not settings.web_auth_enabled:
        return True
    expected_user = (settings.web_auth_user or "").strip()
    expected_pass = (settings.web_auth_password or "").strip()
    return username.strip() == expected_user and password.strip() == expected_pass


def create_session_token() -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = time.time() + _TOKEN_TTL_SEC
    return token


def validate_token(token: str | None) -> bool:
    if not settings.web_auth_enabled:
        return True
    if not token:
        return False
    exp = _TOKENS.get(token)
    if exp is None or exp < time.time():
        _TOKENS.pop(token, None)
        return False
    return True


def revoke_token(token: str) -> None:
    _TOKENS.pop(token, None)


def require_auth(authorization: str | None = Header(None)) -> None:
    if not settings.web_auth_enabled:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="login required")
    token = authorization.removeprefix("Bearer ").strip()
    if not validate_token(token):
        raise HTTPException(status_code=401, detail="invalid or expired session")


AuthDep = Annotated[None, Depends(require_auth)]
