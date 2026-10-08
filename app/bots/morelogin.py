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
import os
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
    """Ошибка MoreLogin. code: profile_missing (профиль не задан) или profile_unavailable (не запустился)."""

    def __init__(self, message: str, code: str = "profile_unavailable") -> None:
        super().__init__(message)
        self.code = code


DEFAULT_HOST = "http://127.0.0.1:40000"
HTTP_TIMEOUT_S = 30.0


def api_host() -> str:
    """Адрес локального API MoreLogin: MORELOGIN_API_URL (settings.py не трогаем), по умолчанию порт 40000."""
    return (os.environ.get("MORELOGIN_API_URL") or DEFAULT_HOST).strip().rstrip("/")


def stop_on_exit_default() -> bool:
    """Закрывать ли профиль при выходе. По умолчанию НЕТ: браузер оператора не закрываем (E03).
    Включается MORELOGIN_STOP_ON_EXIT=1 (только для профиля, который мы сами подняли для робота)."""
    return (os.environ.get("MORELOGIN_STOP_ON_EXIT") or "").strip().lower() in ("1", "true", "yes")


async def _http_post(path: str, profile_id: str, host: str | None = None) -> dict:
    host = host or api_host()
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as s, s.post(f"{host}{path}", json={"profileId": profile_id}) as r:
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
        try:
            data = await _http_post(ep, profile_id)
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            # MoreLogin не запущен / порт не тот: дальше кандидаты не помогут
            raise MoreLoginError(
                f"MoreLogin не отвечает ({type(e).__name__}): запустите MoreLogin и проверьте MORELOGIN_API_URL",
                "profile_unavailable") from e
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
    raise MoreLoginError(f"не удалось запустить профиль …{str(profile_id)[-4:]}: {str(last)[:200]}",
                         "profile_unavailable")


async def stop_profile(profile_id: str) -> None:
    for ep in STOP_ENDPOINTS:
        await _http_post(ep, profile_id)


def resolve_profile(profile_id: str | None = None, *, platform: str | None = None, accounts=None) -> str:
    """Профиль для запуска: явный id, иначе по площадке из конфига аккаунтов, иначе MORELOGIN_PROFILE_ID."""
    if profile_id:
        return profile_id
    from app.services.publish_accounts import load_accounts, select_profile
    if platform:
        cfg = accounts if accounts is not None else load_accounts()
        pid = select_profile(platform, cfg, legacy_profile_id=settings.morelogin_profile_id or "")
    else:
        pid = (settings.morelogin_profile_id or "").strip() or None
    if not pid:
        raise MoreLoginError(
            f"профиль MoreLogin для площадки {platform or '(общий)'} не задан "
            "(data/publish_accounts.json или MORELOGIN_PROFILE_ID)", "profile_missing")
    return pid


@contextlib.asynccontextmanager
async def morelogin_browser(profile_id: str | None = None, *, platform: str | None = None, accounts=None,
                            stop_on_exit: bool | None = None) -> AsyncIterator[Browser]:
    """Контекст-менеджер: открывает MoreLogin-профиль и возвращает Playwright Browser.

    Профиль: явный id, либо по площадке (platform + конфиг аккаунтов), либо MORELOGIN_PROFILE_ID.
    При выходе отключаемся от CDP; сам профиль останавливается только при stop_on_exit=True
    (по умолчанию нет: открытый браузер оператора не закрываем)."""
    pid = resolve_profile(profile_id, platform=platform, accounts=accounts)
    stop = stop_on_exit_default() if stop_on_exit is None else stop_on_exit
    cdp_url = await start_profile(pid)
    logger.info("morelogin started: profile=…{}", str(pid)[-4:])
    pw = await async_playwright().start()
    try:
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:  # noqa: BLE001
            raise MoreLoginError(f"не удалось подключиться к браузеру профиля …{str(pid)[-4:]} ({type(e).__name__})",
                                 "profile_unavailable") from e
        try:
            yield browser
        finally:
            try:
                await browser.close()  # для CDP это отключение, а не закрытие браузера
            except Exception:  # noqa: BLE001
                logger.warning("morelogin: отключение от CDP не удалось, игнорируем")
    finally:
        await pw.stop()
        if stop:
            await asyncio.sleep(0.5)
            try:
                await stop_profile(pid)
            except Exception:  # noqa: BLE001
                logger.warning("morelogin stop_profile failed, игнорируем")
