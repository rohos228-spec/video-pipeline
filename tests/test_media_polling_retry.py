"""Тесты retry-логики на транзиентные HTTP 502/503/504 ошибки в поллерах генерации."""

import pytest
import httpx
from pathlib import Path
from unittest.mock import patch

from app.bots.outsee_http import _poll_generation
from app.bots.kie_kling import _poll_task


@pytest.mark.asyncio
async def test_outsee_poll_recovers_from_transient_502_streak():
    """Проверяет, что Outsee _poll_generation выдерживает временный 502/504 и дожидается успеха."""
    call_count = 0

    def custom_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        # Первые 2 вызова — 502 Bad Gateway
        if call_count <= 2:
            return httpx.Response(502, text="Bad Gateway from Cloudflare")
        # 3-й вызов — processing
        elif call_count == 3:
            return httpx.Response(200, json={"status": "processing"})
        # 4-й вызов — success с URL
        return httpx.Response(200, json={"status": "completed", "result_url": "https://outsee.fake/video.mp4"})

    transport = httpx.MockTransport(custom_handler)
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
        res = await _poll_generation("gen_123", timeout=30.0)
        assert res.get("status") == "completed"
        assert res.get("result_url") == "https://outsee.fake/video.mp4"
        assert call_count == 4


@pytest.mark.asyncio
async def test_kie_kling_poll_recovers_from_transient_504_streak():
    """Проверяет, что Kie Kling _poll_task выдерживает временный 504 Gateway Timeout."""
    call_count = 0

    def custom_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        # Первые 2 вызова — 504 Gateway Timeout
        if call_count <= 2:
            return httpx.Response(504, text="Gateway Timeout")
        # 3-й вызов — success
        return httpx.Response(200, json={"code": 0, "data": {"state": "success", "resultJson": "{\"videoUrl\":\"https://kling.fake/v.mp4\"}"}})

    transport = httpx.MockTransport(custom_handler)
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
        res = await _poll_task("task_kling_999", timeout_s=30.0)
        assert res.get("state") == "success"
        assert call_count == 3