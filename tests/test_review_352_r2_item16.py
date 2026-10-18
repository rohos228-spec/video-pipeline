"""Повторное ревью #352 №16: ReadTimeout не шлёт генерацию повторно."""

from __future__ import annotations

from pathlib import Path


def test_generate_retry_is_connect_only() -> None:
    text = Path("app/bots/outsee_http.py").read_text(encoding="utf-8")
    assert "isinstance(e, (httpx.ConnectTimeout, httpx.ConnectError))" in text
    assert "httpx.TimeoutException" not in text
    assert "httpx.ReadTimeout" not in text
