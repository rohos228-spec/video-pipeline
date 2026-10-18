"""Неблокирующее ревью #352: пароль студии через compare_digest, код не в stdout."""

from __future__ import annotations

import inspect


def test_legacy_password_compare_digest(monkeypatch) -> None:
    from app.settings import settings
    from app.web.routers.auth import legacy_studio_password_ok, login

    monkeypatch.setattr(settings, "web_auth_user", "studio")
    monkeypatch.setattr(settings, "web_auth_password", "s3cret-pass")
    assert legacy_studio_password_ok("studio", "s3cret-pass") is True
    assert legacy_studio_password_ok("studio", "wrong-pass") is False
    assert legacy_studio_password_ok("other", "s3cret-pass") is False
    src = inspect.getsource(login)
    assert "compare_digest" not in src
    assert "legacy_studio_password_ok" in src
    assert "body.password ==" not in src

