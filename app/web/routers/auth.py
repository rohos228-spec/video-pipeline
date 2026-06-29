"""Login / logout для fleet UI."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.settings import settings
from app.web.auth_sessions import create_session_token, revoke_token, verify_credentials

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginBody) -> dict:
    if not settings.web_auth_enabled:
        return {"token": "", "auth_required": False}
    if not verify_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return {"token": create_session_token(), "auth_required": True}


@router.post("/logout")
async def logout(authorization: str | None = None) -> dict:
    if authorization and authorization.startswith("Bearer "):
        revoke_token(authorization.removeprefix("Bearer ").strip())
    return {"ok": True}


@router.get("/status")
async def auth_status() -> dict:
    return {"auth_required": settings.web_auth_enabled}
