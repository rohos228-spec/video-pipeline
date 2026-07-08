"""Тесты CDP: детект зависания и file-lock."""

from __future__ import annotations

import pytest

from app.bots import chrome_cdp as cdp


def test_playwright_cdp_hang_ws_connected():
    exc = RuntimeError(
        "BrowserType.connect_over_cdp: Timeout 45000ms exceeded.\n"
        "Call log:\n  - <ws connected> ws://127.0.0.1:29229/..."
    )
    assert cdp.playwright_cdp_hang(exc) is True


def test_playwright_cdp_hang_other_timeout():
    assert cdp.playwright_cdp_hang(TimeoutError("page load timeout")) is False


@pytest.mark.asyncio
async def test_cdp_connect_lock_exclusive(tmp_path, monkeypatch):
    lock_path = tmp_path / ".cdp_connect.lock"
    monkeypatch.setattr(cdp, "_CDP_CONNECT_LOCK_PATH", lock_path)

    async with cdp.cdp_connect_lock():
        assert lock_path.is_file()
        with pytest.raises(RuntimeError, match="занят"):
            async with cdp.cdp_connect_lock(wait_sec=0.5):
                pass

    assert not lock_path.exists()
