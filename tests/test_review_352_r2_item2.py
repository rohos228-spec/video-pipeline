"""Повторное ревью #352 №2: ключи только для сессии, маска без кусков секрета."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.settings import settings
from app.web.auth_sessions import issue_session_token
from app.web.routers.profile import KeyUpdate, _caller_is_studio_admin, get_keys, put_key


def _request(method: str, authorization: str | None = None) -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": "/profile/keys",
            "raw_path": b"/profile/keys",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 9),
            "server": ("test", 80),
        }
    )


@pytest.mark.asyncio
async def test_get_keys_requires_session_and_hides_key_edges(monkeypatch) -> None:
    secret = "sk-ABCDEFGHIJKLMNOP"
    monkeypatch.setattr(settings, "web_auth_user", "studio")
    monkeypatch.setattr(settings, "web_auth_password", "secret-pass")
    monkeypatch.setattr(settings, "gpt_api_key", secret)
    with pytest.raises(HTTPException) as exc:
        await get_keys(_request("GET"))
    assert exc.value.status_code == 401
    token = issue_session_token("studio")
    rows = await get_keys(_request("GET", f"Bearer {token}"))
    gpt = next(row for row in rows if row["id"] == "gpt")
    assert gpt["configured"] is True
    assert secret[:3] not in gpt["masked"]
    assert secret[-4:] not in gpt["masked"]
    assert secret not in gpt["masked"]


def test_only_studio_admin_may_change_keys(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.web.user_accounts.user_for_session",
        lambda token: {"role": "user"} if token == "user" else {"role": "admin"},
    )
    legacy = issue_session_token("studio")
    assert _caller_is_studio_admin(legacy) is True
    assert _caller_is_studio_admin("admin-db") is True
    assert _caller_is_studio_admin("user") is False


@pytest.mark.asyncio
async def test_put_key_rejects_non_admin_session(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text("GPT_API_KEY=untouched\n", encoding="utf-8")
    monkeypatch.setattr("app.web.routers.profile._ENV", env)
    monkeypatch.setattr(
        "app.web.routers.profile._BACKUP_DIR",
        tmp_path / "data" / "profile-env-backups",
    )
    monkeypatch.setattr(settings, "web_auth_user", "studio")
    monkeypatch.setattr(settings, "web_auth_password", "secret-pass")

    async def _allow(_request):
        return "user-session"

    monkeypatch.setattr("app.web.routers.profile.require_web_user", _allow)
    monkeypatch.setattr(
        "app.web.user_accounts.user_for_session",
        lambda token: {"role": "user"},
    )
    with pytest.raises(HTTPException) as exc:
        await put_key("gpt", KeyUpdate(value="should-not-land"), _request("PUT", "Bearer user-session"))
    assert exc.value.status_code == 403
    assert env.read_text(encoding="utf-8") == "GPT_API_KEY=untouched\n"
