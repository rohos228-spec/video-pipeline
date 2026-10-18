"""Повторное ревью #352 №1: users.db не обходит пароль студии."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.settings import settings
from app.web.auth_sessions import issue_session_token, validate_session_token
from app.web.routers.auth import RegisterBody, register


def _request() -> object:
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/auth/register",
            "raw_path": b"/auth/register",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 9),
            "server": ("test", 80),
        }
    )


def test_user_db_session_does_not_bypass_studio_auth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "web_auth_user", "studio")
    monkeypatch.setattr(settings, "web_auth_password", "secret-pass")
    monkeypatch.setattr(
        "app.web.user_accounts.user_for_session",
        lambda token: {"role": "admin"} if token == "db-session" else None,
    )
    assert validate_session_token("db-session") is False
    legacy = issue_session_token("studio")
    assert validate_session_token(legacy) is True


@pytest.mark.asyncio
async def test_register_is_closed_while_studio_auth_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "web_auth_user", "studio")
    monkeypatch.setattr(settings, "web_auth_password", "secret-pass")
    body = RegisterBody(
        email="a@example.com",
        username="operator",
        name="Op",
        password="secret123",
    )
    with pytest.raises(HTTPException) as exc:
        await register(body, _request())
    assert exc.value.status_code == 403
