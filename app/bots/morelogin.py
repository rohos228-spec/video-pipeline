"""MoreLogin — запуск браузерного профиля через локальный API MoreLogin и
подключение к нему через Playwright CDP.

Как это работает:
  1. MoreLogin локально поднимает API-сервер на http://127.0.0.1:40000 (порт по умолчанию).
  2. Мы вызываем `/api/v1/profile-browser/start?profileId=...` — MoreLogin запускает
     Chromium c нужным fingerprint-ом и в ответе возвращает CDP-endpoint (ws://...).
  3. Подключаемся к CDP, берём существующий контекст и вкладку.

⚠️ Формат API в разных версиях MoreLogin меняется. В рантайме берём первый
рабочий путь из кандидатов, логируем 4xx для диагностики. Параметры настраиваются
через `.env`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import aiohttp
from loguru import logger
from playwright.async_api import Browser, async_playwright

from app.settings import settings

# кандидаты путей разных версий MoreLogin API
START_ENDPOINTS = [
    "/api/v1/profile-browser/start",
    "/api/v1/browser/start",
    "/v1/profile/open",
]
STOP_ENDPOINTS = [
    "/api/v1/profile-browser/stop",
    "/api/v1/browser/stop",
    "/v1/profile/close",
]


class MoreLoginError(RuntimeError):
    pass


async def _http_post(path: str, profile_id: str, host: str = "http://127.0.0.1:40000") -> dict:
    async with aiohttp.ClientSession() as s, s.post(f"{host}{path}", json={"profileId": profile_id}) as r:
        text = await r.text()
        logger.debug("morelogin {} {} {} → {}", path, profile_id, r.status, text[:400])
        if r.status != 200:
            return {"__status": r.status, "__text": text}
        try:
            return await r.json(content_type=None)
        except Exception:  # noqa: BLE001
            return {"__status": r.status, "__text": text}


async def start_profile(profile_id: str) -> str:
    """Запустить профиль и вернуть CDP endpoint URL."""
    last = None
    for ep in START_ENDPOINTS:
        data = await _http_post(ep, profile_id)
        # Возможные ключи ответа:
        # MoreLogin 2.x: {"data": {"ws": "ws://..."}} или {"data": {"debuggerAddress": "127.0.0.1:port"}}
        body = data.get("data") if isinstance(data, dict) else None
        if isinstance(body, dict):
            ws = body.get("ws") or body.get("webSocketDebuggerUrl")
            addr = body.get("debuggerAddress") or body.get("debugger")
            if ws:
                return ws
            if addr:
                return f"http://{addr}"
        last = data
    raise MoreLoginError(f"не удалось запустить профиль {profile_id}: {last}")


async def stop_profile(profile_id: str) -> None:
    for ep in STOP_ENDPOINTS:
        await _http_post(ep, profile_id)


@contextlib.asynccontextmanager
async def morelogin_browser(profile_id: str | None = None) -> AsyncIterator[Browser]:
    """Контекст-менеджер: открывает MoreLogin-профиль и возвращает Playwright Browser.
    При выходе закрывает профиль (если мы его стартовали)."""
    pid = profile_id or settings.morelogin_profile_id
    if not pid:
        raise MoreLoginError("MORELOGIN_PROFILE_ID не задан в .env")
    cdp_url = await start_profile(pid)
    logger.info("morelogin started: cdp={}", cdp_url)
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        try:
            yield browser
        finally:
            await browser.close()
    finally:
        await pw.stop()
        # маленькая пауза и просим остановить профиль
        await asyncio.sleep(0.5)
        try:
            await stop_profile(pid)
        except Exception:  # noqa: BLE001
            logger.warning("morelogin stop_profile failed, игнорируем")
