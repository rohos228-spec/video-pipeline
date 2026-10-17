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
