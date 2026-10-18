"""П.11: код и ссылка сброса не попадают в лог и в ответ без dev-флага."""

from __future__ import annotations

import logging

import pytest
from loguru import logger

from app.web import user_accounts as ua


def test_smtp_failure_does_not_print_reset_token(capsys, caplog, monkeypatch) -> None:
    secret = "reset-token-not-for-logs"
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("SMTP_PORT", "587")

    class _Boom:
        def __init__(self, *_a, **_k):
            raise OSError("connection refused")

    monkeypatch.setattr(ua.smtplib, "SMTP", _Boom)
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="ERROR")
    try:
        with caplog.at_level(logging.ERROR, logger="via.auth"):
            assert ua.send_mail("a@b.c", "verify", f"link {secret}") is False
    finally:
        logger.remove(sink)
    captured = capsys.readouterr()
    blob = captured.out + captured.err + caplog.text + "\n".join(lines)
    assert secret not in blob
    assert "link " not in captured.out


@pytest.mark.asyncio
async def test_forgot_response_has_no_dev_link(monkeypatch) -> None:
    from app.web.routers import auth as auth_router

    monkeypatch.delenv("VP_AUTH_DEV_MAIL", raising=False)
    monkeypatch.setattr(auth_router.ua, "get_by_login", lambda _email: {"id": 1, "email": "a@b.c"})
    monkeypatch.setattr(auth_router.ua, "issue_token", lambda *_a, **_k: "secret-reset-token")
    monkeypatch.setattr(auth_router.ua, "send_mail", lambda *_a, **_k: False)
    monkeypatch.setattr(auth_router.ua, "mail_configured", lambda: False)

    class _Req:
        headers = {"user-agent": "test"}

    out = await auth_router.forgot(auth_router.EmailBody(email="a@b.c"), _Req())  # type: ignore[arg-type]
    assert "dev_link" not in out or not out.get("dev_link")
    assert "secret-reset-token" not in str(out)
