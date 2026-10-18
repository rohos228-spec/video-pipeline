"""№24: смена ключа требует сессию, бэкап лежит в data/, не в корне репо."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.settings import settings
from app.web.routers.profile import KeyUpdate, put_key


def _request(authorization: str | None = None) -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "PUT",
            "scheme": "http",
            "path": "/profile/keys/gpt",
            "raw_path": b"/profile/keys/gpt",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 9),
            "server": ("test", 80),
        }
    )


@pytest.mark.asyncio
async def test_put_key_requires_session_when_auth_on(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text("GPT_API_KEY=untouched\n", encoding="utf-8")
    monkeypatch.setattr("app.web.routers.profile._ENV", env)
    monkeypatch.setattr("app.web.routers.profile._BACKUP_DIR", tmp_path / "data" / "profile-env-backups")
    monkeypatch.setattr(settings, "web_auth_user", "studio")
    monkeypatch.setattr(settings, "web_auth_password", "secret-pass")
    with pytest.raises(HTTPException) as exc:
        await put_key("gpt", KeyUpdate(value="should-not-land"), _request())
    assert exc.value.status_code == 401
    assert env.read_text(encoding="utf-8") == "GPT_API_KEY=untouched\n"
    assert not (tmp_path / ".env.bak-profile-1").exists()
    assert list(tmp_path.glob(".env.bak-profile-*")) == []


@pytest.mark.asyncio
async def test_put_key_backup_stays_under_data(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text("GPT_API_KEY=old\n", encoding="utf-8")
    backup_dir = tmp_path / "data" / "profile-env-backups"
    monkeypatch.setattr("app.web.routers.profile._ENV", env)
    monkeypatch.setattr("app.web.routers.profile._BACKUP_DIR", backup_dir)
    monkeypatch.setattr(settings, "web_auth_user", "")
    monkeypatch.setattr(settings, "web_auth_password", "")
    monkeypatch.setenv("GPT_API_KEY", "old")
    previous = getattr(settings, "gpt_api_key", "")
    try:
        out = await put_key("gpt", KeyUpdate(value="new-local-value"), _request())
    finally:
        try:
            setattr(settings, "gpt_api_key", previous)
        except Exception:
            pass
    assert out["configured"] is True
    assert "new-local-value" not in str(out)
    text = env.read_text(encoding="utf-8")
    assert "GPT_API_KEY=new-local-value" in text
    backups = list(backup_dir.glob("env-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "GPT_API_KEY=old\n"
    assert list(tmp_path.glob(".env.bak-profile-*")) == []
    assert "bak-profile" not in backups[0].name
