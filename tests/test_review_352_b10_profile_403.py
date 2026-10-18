"""П.10: запись ключей без токена — 403, даже если вход студии выключен."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.settings import settings
from app.web.routers.profile import KeyUpdate, put_key


def _request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": "/api/profile/keys/outsee",
        "raw_path": b"/api/profile/keys/outsee",
        "query_string": b"",
        "headers": [(b"origin", b"http://203.0.113.9")],
        "client": ("203.0.113.9", 443),
        "server": ("127.0.0.1", 8765),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_put_key_without_token_is_403(monkeypatch) -> None:
    monkeypatch.setattr(type(settings), "web_auth_enabled", property(lambda self: False))
    with pytest.raises(HTTPException) as exc:
        await put_key("outsee", KeyUpdate(value="not-a-real-secret"), _request())
    assert exc.value.status_code == 403
