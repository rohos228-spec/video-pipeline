"""Неблокирующее: cookie сессии с Secure на https, код подтверждения не в UI."""

from pathlib import Path


def test_session_cookie_secure_and_no_dev_code_in_ui() -> None:
    src = Path("web/src/components/auth/auth-pages.tsx").read_text(encoding="utf-8")
    assert "; secure" in src
    assert "samesite=lax" in src
    assert "vp-dev-code" not in src
    assert "dev_code" not in src
    assert "Код:" not in src
