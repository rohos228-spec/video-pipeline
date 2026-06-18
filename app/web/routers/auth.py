"""Web login for fleet hub (optional)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.settings import settings
from app.web.auth_sessions import issue_session_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
async def login(body: LoginBody) -> dict:
    if not settings.web_auth_enabled:
        return {"ok": True, "token": None, "auth_required": False}
    user = settings.web_auth_user.strip()
    pwd = settings.web_auth_password
    if body.username != user or body.password != pwd:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = issue_session_token(body.username)
    return {"ok": True, "token": token, "auth_required": True}


@router.get("/status")
async def auth_status() -> dict:
    return {"auth_required": settings.web_auth_enabled}
