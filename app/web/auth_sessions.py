"""Optional web UI session auth for fleet hub."""

from __future__ import annotations

import secrets
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.settings import settings

_TOKENS: dict[str, float] = {}
_TOKEN_TTL_SEC = 86400 * 7


def issue_session_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = time.time()
    return token


def validate_session_token(token: str | None) -> bool:
    if not settings.web_auth_enabled:
        return True
    if not token:
        return False
    issued = _TOKENS.get(token)
    if issued is None:
        return False
    if time.time() - issued > _TOKEN_TTL_SEC:
        _TOKENS.pop(token, None)
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
