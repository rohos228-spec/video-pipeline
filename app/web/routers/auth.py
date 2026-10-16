
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.services import access_guard
from app.settings import settings
from app.web import auth_sessions
from app.web.auth_sessions import AuthDep, issue_session_token

router = APIRouter(prefix="/auth", tags=["auth"])

FAILED_LOGIN_DELAY_S = 0.4


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


def _throttle_keys(request: Request, username: str) -> list[str]:
    host = request.client.host if request.client else "-"
    return [f"ip:{host}", f"user:{username.strip().lower()}"]


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    if not settings.web_auth_enabled:
        return {"ok": True, "token": None, "auth_required": False}
    keys = _throttle_keys(request, body.username)
    wait = auth_sessions.login_throttle.retry_after(keys)
    if wait:
        raise HTTPException(
            status_code=429, headers={"Retry-After": str(wait)},
            detail={"code": "too_many_attempts",
                    "message": f"Слишком много неудачных попыток входа. Повторите через {wait} с."})
    if not auth_sessions.verify_credentials(body.username, body.password):
        auth_sessions.login_throttle.record_failure(keys)
        if FAILED_LOGIN_DELAY_S > 0:
            await asyncio.sleep(FAILED_LOGIN_DELAY_S)
        raise HTTPException(status_code=401, detail="invalid credentials")
    auth_sessions.login_throttle.record_success(keys)
    token = issue_session_token(body.username)
    response.set_cookie("vp_session", token, max_age=auth_sessions._TOKEN_TTL_SEC, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")
    return {"ok": True, "token": token, "auth_required": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    token = auth_sessions._extract_bearer(request)
    auth_sessions.revoke_session_token(token)
    response.delete_cookie("vp_session", path="/")
    return {"ok": True}


@router.get("/status")
async def auth_status() -> dict:
    return {"auth_required": settings.web_auth_enabled}


@router.get("/operator-token", dependencies=[Depends(access_guard.local_only_when_no_auth)])
async def operator_token(request: Request, operator: AuthDep) -> dict:
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    cross_site = (request.headers.get("sec-fetch-site") or "").lower() == "cross-site"
    if cross_site or (origin is not None and not access_guard.origin_allowed(origin, host)):
        raise HTTPException(status_code=403, detail={
            "code": "bad_origin",
            "message": "Токен оператора выдаётся только странице самого Studio."})
    return {"token": access_guard.get_operator_token(), "header": access_guard.OPERATOR_HEADER,
            "required": access_guard.operator_token_enforced()}
