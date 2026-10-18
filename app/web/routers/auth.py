"""Web auth for VIA. Legacy (main): single env user WEB_AUTH_USER/WEB_AUTH_PASSWORD, opaque tokens.
New: registered users (data/users.db), email verification code, password reset tokens.
Enforcement stays OFF unless WEB_AUTH_USER+WEB_AUTH_PASSWORD are set (settings.web_auth_enabled)."""

from __future__ import annotations

import asyncio
import hmac
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.services import access_guard
from app.settings import settings
from app.web import auth_sessions
from app.web import user_accounts as ua
from app.web.auth_sessions import AuthDep, _extract_bearer, issue_session_token, require_web_user

FAILED_LOGIN_DELAY_S = 0.4

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USER = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


def _pw_ok(pw: str) -> bool:
    return len(pw) >= 8 and re.search(r"[A-Za-zА-Яа-я]", pw) and re.search(r"\d", pw)


def legacy_studio_password_ok(username: str, password: str) -> bool:
    """Сравнение логина и пароля студии за постоянное время."""
    if not settings.web_auth_enabled:
        return False
    try:
        user_ok = hmac.compare_digest(
            username.encode("utf-8"),
            settings.web_auth_user.strip().encode("utf-8"),
        )
        pass_ok = hmac.compare_digest(
            password.encode("utf-8"),
            settings.web_auth_password.encode("utf-8"),
        )
    except (TypeError, ValueError):
        return False
    return bool(user_ok and pass_ok)


def reset_link_base() -> str:
    """База ссылки сброса только из настроек, не из заголовка Origin."""
    public = (settings.fleet_public_url or "").strip().rstrip("/")
    if public:
        return public
    return settings.fleet_local_web_url


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=200)  # username or email
    password: str = Field(min_length=1, max_length=200)


class RegisterBody(BaseModel):
    email: str = Field(max_length=200)
    username: str = Field(max_length=32)
    name: str = Field("", max_length=80)
    password: str = Field(max_length=200)
    plan: str | None = Field(None, max_length=20)


class EmailBody(BaseModel):
    email: str = Field(max_length=200)


class ResetBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=200)


class VerifyBody(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class DeleteBody(BaseModel):
    password: str


def _send_verify(req: Request, u) -> dict:
    code = ua.issue_token(u["id"], "verify")
    sent = ua.send_mail(u["email"], "VIA — код подтверждения", f"Ваш код подтверждения VIA: {code} (действует 24 ч)")
    out = {"mail_sent": sent}
    if os.environ.get("VP_AUTH_DEV_MAIL") == "1":
        out["dev_code"] = code
    return out


def _me(req: Request):
    u = ua.user_for_session(_extract_bearer(req))
    if not u:
        raise HTTPException(401, "login required")
    return u


@router.get("/status")
async def auth_status(request: Request) -> dict:
    u = ua.user_for_session(_extract_bearer(request))
    return {"auth_required": settings.web_auth_enabled, "registration": True, "mail_configured": ua.mail_configured(),
            "user": ua.public(u) if u else None}


@router.post("/register")
async def register(body: RegisterBody, request: Request) -> dict:
    if settings.web_auth_enabled:
        raise HTTPException(403, "Регистрация закрыта, пока включён вход студии")
    email, username = body.email.strip().lower(), body.username.strip()
    if not _EMAIL.match(email):
        raise HTTPException(422, "Некорректный email")
    if not _USER.match(username):
        raise HTTPException(422, "Логин: 3–32 символа, латиница, цифры, _ . -")
    if not _pw_ok(body.password):
        raise HTTPException(422, "Пароль: минимум 8 символов, буквы и цифры")
    try:
        plan = (body.plan or "").lower()
        u = ua.create_user(email, username, body.name.strip() or username, body.password,
                           plan if plan in ("start", "studio", "factory") else None)
    except ValueError as e:
        raise HTTPException(409, "Email уже зарегистрирован" if str(e) == "email_taken" else "Логин занят")
    token = ua.new_session(u["id"], request.headers.get("user-agent", ""))
    return {"ok": True, "token": token, "user": u, **_send_verify(request, u)}


def _throttle_keys(request: Request, username: str) -> list[str]:
    host = request.client.host if request.client else "-"
    return [f"ip:{host}", f"user:{username.strip().lower()}"]


def _session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        "vp_session", token, max_age=auth_sessions._TOKEN_TTL_SEC, httponly=True,
        samesite="strict", secure=request.url.scheme == "https", path="/",
    )


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
                    "message": f"Слишком много неудачных попыток входа. Повторите через {wait} с."},
        )
    u = ua.get_by_login(body.username.strip())
    if u and ua.verify_password(body.password, u["password_hash"]):
        auth_sessions.login_throttle.record_success(keys)
        token = ua.new_session(u["id"], request.headers.get("user-agent", ""))
        _session_cookie(response, request, token)
        return {"ok": True, "token": token, "auth_required": settings.web_auth_enabled, "user": ua.public(u)}
    # Пароль студии в settings может быть хешем (W1.2, verify_credentials) или открытым env-значением (main).
    # Обе проверки выполняются всегда — без короткого замыкания по времени.
    hashed_ok = auth_sessions.verify_credentials(body.username, body.password)
    legacy_ok = legacy_studio_password_ok(body.username, body.password)
    if hashed_ok or legacy_ok:
        auth_sessions.login_throttle.record_success(keys)
        token = issue_session_token(body.username)
        _session_cookie(response, request, token)
        return {"ok": True, "token": token, "auth_required": True, "user": None}
    auth_sessions.login_throttle.record_failure(keys)
    if FAILED_LOGIN_DELAY_S > 0:
        await asyncio.sleep(FAILED_LOGIN_DELAY_S)
    raise HTTPException(401, "Неверный логин или пароль")


@router.get("/me")
async def me(request: Request) -> dict:
    return {"user": ua.public(_me(request))}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    tok = _extract_bearer(request)
    if tok:
        ua.drop_session(tok)
        auth_sessions.revoke_session_token(tok)
    response.delete_cookie("vp_session", path="/")
    return {"ok": True}


@router.post("/verify", dependencies=[Depends(require_web_user)])
async def verify(body: VerifyBody, request: Request) -> dict:
    u = _me(request)
    if not ua.consume_verify(u["id"], body.code.strip()):
        raise HTTPException(400, "Неверный или просроченный код")
    return {"ok": True}


@router.post("/verify/resend", dependencies=[Depends(require_web_user)])
async def verify_resend(request: Request) -> dict:
    return {"ok": True, **_send_verify(request, _me(request))}


@router.post("/forgot")
async def forgot(body: EmailBody, request: Request) -> dict:
    u = ua.get_by_login(body.email.strip())
    out = {"ok": True, "mail_sent": False, "dev_note": None}
    if u:  # same answer whether or not the user exists
        link = f"{reset_link_base()}/reset-password?token={ua.issue_token(u['id'], 'reset')}"
        sent = ua.send_mail(u["email"], "VIA — восстановление пароля", f"Ссылка для смены пароля (1 ч): {link}")
        out.update(mail_sent=sent)
        if not sent:
            out["dev_note"] = "Почта не настроена — ссылка записана в лог сервера"
    elif not ua.mail_configured():
        out["dev_note"] = "Почта не настроена — ссылка записана в лог сервера (если аккаунт существует)"
    return out


@router.get("/reset/check")
async def reset_check(token: str) -> dict:
    return {"valid": ua.check_reset(token)}


@router.post("/reset")
async def reset(body: ResetBody) -> dict:
    if not _pw_ok(body.password):
        raise HTTPException(422, "Пароль: минимум 8 символов, буквы и цифры")
    r = ua.consume_reset(body.token)
    if not r:
        raise HTTPException(400, "Ссылка недействительна или устарела")
    ua.set_password(r["user_id"], body.password)
    return {"ok": True}


@router.post("/delete", dependencies=[Depends(require_web_user)])
async def delete_account(body: DeleteBody, request: Request) -> dict:
    u = _me(request)
    if not ua.verify_password(body.password, u["password_hash"]):
        raise HTTPException(401, "Неверный пароль")
    ua.delete_user(u["id"])
    return {"ok": True}


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
