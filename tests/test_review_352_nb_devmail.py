"""Неблокирующее ревью #352: код верификации не печатается в stdout."""

from __future__ import annotations


def test_dev_mail_does_not_print_code(capsys, monkeypatch) -> None:
    from app.web import user_accounts as ua

    monkeypatch.delenv("SMTP_HOST", raising=False)
    secret = "verify-code-918273"
    assert ua.send_mail("a@b.c", "verify", f"code {secret}") is False
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_smtp_failure_does_not_print_code(capsys, caplog, monkeypatch) -> None:
    import logging

    from loguru import logger

    from app.web import user_accounts as ua

    secret = "verify-code-smtp-445566"
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("SMTP_PORT", "587")

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise OSError("connection refused")

    monkeypatch.setattr(ua.smtplib, "SMTP", _Boom)
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="ERROR")
    try:
        with caplog.at_level(logging.ERROR, logger="via.auth"):
            assert ua.send_mail("a@b.c", "verify", f"code {secret}") is False
    finally:
        logger.remove(sink)
    captured = capsys.readouterr()
    blob = captured.out + captured.err + caplog.text + "\n".join(lines)
    assert secret not in blob
    assert "code " not in captured.out
    assert any("was not sent" in rec.message for rec in caplog.records)
