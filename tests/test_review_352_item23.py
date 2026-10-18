"""№23: код подтверждения и ссылка сброса не возвращаются в JSON."""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from app.web.routers.auth import EmailBody, _send_verify, forgot


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/auth/forgot",
            "raw_path": b"/auth/forgot",
            "query_string": b"",
            "headers": [(b"origin", b"http://studio.local")],
            "client": ("127.0.0.1", 9),
            "server": ("studio.local", 80),
        }
    )


def test_send_verify_keeps_code_out_of_json(monkeypatch) -> None:
    secret = "918273"
    mailed: dict[str, str] = {}

    def _mail(_to: str, _subject: str, body: str) -> bool:
        mailed["body"] = body
        return False

    monkeypatch.setattr("app.web.routers.auth.ua.issue_token", lambda *_a, **_k: secret)
    monkeypatch.setattr("app.web.routers.auth.ua.send_mail", _mail)
    out = _send_verify(_request(), {"id": 3, "email": "a@b.c"})
    blob = json.dumps(out, ensure_ascii=False)
    assert secret not in blob
    assert "dev_code" not in out
    assert secret in mailed["body"]


@pytest.mark.asyncio
async def test_forgot_keeps_reset_link_out_of_json(monkeypatch) -> None:
    secret = "reset-token-not-for-json"
    mailed: dict[str, str] = {}

    def _mail(_to: str, _subject: str, body: str) -> bool:
        mailed["body"] = body
        return False

    monkeypatch.setattr(
        "app.web.routers.auth.ua.get_by_login",
        lambda _login: {"id": 4, "email": "a@b.c"},
    )
    monkeypatch.setattr("app.web.routers.auth.ua.issue_token", lambda *_a, **_k: secret)
    monkeypatch.setattr("app.web.routers.auth.ua.send_mail", _mail)
    out = await forgot(EmailBody(email="a@b.c"), _request())
    blob = json.dumps(out, ensure_ascii=False)
    assert secret not in blob
    assert "dev_link" not in out
    assert secret in mailed["body"]
    assert out["mail_sent"] is False
    assert out["dev_note"]
