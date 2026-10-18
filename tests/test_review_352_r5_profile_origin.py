"""Ревью #352 п.13: запись ключей без auth смотрит Origin и loopback."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.settings import settings
from app.web.routers.profile import KeyUpdate, put_key


def _request(*, origin: str | None, client: str) -> Request:
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "PUT",
            "scheme": "http",
            "path": "/api/profile/keys/gpt",
            "raw_path": b"/api/profile/keys/gpt",
            "query_string": b"",
            "headers": headers,
            "client": (client, 9),
            "server": ("127.0.0.1", 80),
        }
    )


@pytest.mark.asyncio
async def test_put_key_foreign_origin_and_remote_without_origin_are_403(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(type(settings), "web_auth_enabled", property(lambda self: False))
    monkeypatch.setattr(settings, "web_auth_user", "")
    monkeypatch.setattr(settings, "web_auth_password", "")
    env = tmp_path / ".env"
    env.write_text("GPT_API_KEY=old\n", encoding="utf-8")
    monkeypatch.setattr("app.web.routers.profile._ENV", env)
    monkeypatch.setattr("app.web.routers.profile._BACKUP_DIR", tmp_path / "bak")
    monkeypatch.setenv("GPT_API_KEY", "old")

    with pytest.raises(HTTPException) as foreign:
        await put_key(
            "gpt",
            KeyUpdate(value="foreign-value"),
            _request(origin="http://203.0.113.9", client="203.0.113.9"),
        )
    assert foreign.value.status_code == 403

    with pytest.raises(HTTPException) as remote:
        await put_key(
            "gpt",
            KeyUpdate(value="remote-value"),
            _request(origin=None, client="203.0.113.9"),
        )
    assert remote.value.status_code == 403
    assert env.read_text(encoding="utf-8") == "GPT_API_KEY=old\n"

    out = await put_key(
        "gpt",
        KeyUpdate(value="loopback-value"),
        _request(origin=None, client="127.0.0.1"),
    )
    assert out["configured"] is True
    assert "loopback-value" not in str(out)
