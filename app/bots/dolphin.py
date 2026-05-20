"""Dolphin Anty — запуск антидетект-профиля через локальный API Dolphin Anty
и подключение к нему по Playwright CDP. Используется на шаге 10 (озвучка через
11Labs), потому что 11Labs агрессивно лочит обычные браузерные сессии.

Как работает:
  1. Dolphin Anty локально поднимает Local API (по умолчанию http://127.0.0.1:3001;
     включается в Settings → API). Авторизация — Bearer-токен из Dolphin Anty
     (Settings → API → Generate token).
  2. POST `/v1.0/browser_profiles/{profile_id}/start?automation=1` запускает
     профиль с CDP-портом. В ответе — `{"automation": {"port": <int>, "wsEndpoint": "..."}}`.
  3. Подключаемся к CDP через Playwright, берём первый контекст / создаём вкладку.
  4. На выходе зовём `/v1.0/browser_profiles/{profile_id}/stop`.

Настройки берём из `app.settings`:
  - `dolphin_api_host`   (DOLPHIN_API_HOST,   default http://127.0.0.1:3001)
  - `dolphin_api_token`  (DOLPHIN_API_TOKEN)
  - `dolphin_profile_id` (DOLPHIN_PROFILE_ID)

⚠️ Формат API в разных версиях Dolphin Anty может отличаться — пробуем
последовательно набор эндпойнтов / ключей ответа, логируем 4xx/5xx для
диагностики.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import aiohttp
from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.settings import settings

# Кандидаты эндпойнтов разных версий Dolphin Anty Local API.
START_ENDPOINTS = (
    "/v1.0/browser_profiles/{pid}/start?automation=1",
    "/browser_profiles/{pid}/start?automation=1",
    "/v1.0/browser_profiles/start?automation=1&profileId={pid}",
)
STOP_ENDPOINTS = (
    "/v1.0/browser_profiles/{pid}/stop",
    "/browser_profiles/{pid}/stop",
    "/v1.0/browser_profiles/stop?profileId={pid}",
)


class DolphinError(RuntimeError):
    """Любая ошибка взаимодействия с Dolphin Anty Local API."""


def _auth_headers() -> dict[str, str]:
    tok = settings.dolphin_api_token or ""
    if not tok:
        return {}
    return {"Authorization": f"Bearer {tok}"}


async def _http_get(path: str) -> dict:
    url = settings.dolphin_api_host.rstrip("/") + path
    async with aiohttp.ClientSession(headers=_auth_headers()) as s, s.get(url) as r:
        text = await r.text()
        logger.debug("dolphin GET {} → {} {}", path, r.status, text[:400])
        if r.status >= 400:
            return {"__status": r.status, "__text": text}
        try:
            return await r.json(content_type=None)
        except Exception:  # noqa: BLE001
            return {"__status": r.status, "__text": text}


def _extract_cdp_endpoint(body: dict) -> str | None:
    """Найти CDP endpoint в ответе start. Разные версии Dolphin Anty кладут
    его в разные поля — пробуем все известные ключи."""
    if not isinstance(body, dict):
        return None
    # Возможные формы:
    #  {"automation": {"port": 1234, "wsEndpoint": "ws://..."}}
    #  {"data": {"port": 1234}}
    #  {"port": 1234}
    #  {"wsEndpoint": "ws://..."}
    for key in ("automation", "data"):
        sub = body.get(key)
        if isinstance(sub, dict):
            ws = sub.get("wsEndpoint") or sub.get("ws")
            if ws:
                return ws
            port = sub.get("port")
            if isinstance(port, int) and port > 0:
                return f"http://127.0.0.1:{port}"
    ws = body.get("wsEndpoint") or body.get("ws")
    if ws:
        return ws
    port = body.get("port")
    if isinstance(port, int) and port > 0:
        return f"http://127.0.0.1:{port}"
    return None


async def start_profile(profile_id: str) -> str:
    """Запустить профиль в Dolphin Anty и вернуть CDP endpoint.

    Пробуем все кандидатные эндпойнты до первого успешного (200 + извлечённый
    CDP-адрес). На любую другую ошибку — DolphinError.
    """
    last: dict | None = None
    for tmpl in START_ENDPOINTS:
        path = tmpl.format(pid=profile_id)
        body = await _http_get(path)
        if isinstance(body, dict) and "__status" not in body:
            cdp = _extract_cdp_endpoint(body)
            if cdp:
                logger.info("dolphin: profile {} started, cdp={}", profile_id, cdp)
                return cdp
        last = body
    raise DolphinError(
        f"не удалось запустить Dolphin профиль {profile_id}: {last}"
    )


async def stop_profile(profile_id: str) -> None:
    """Остановить профиль. Ошибки логируем, но не пробрасываем — на выходе
    важнее не упасть."""
    for tmpl in STOP_ENDPOINTS:
        path = tmpl.format(pid=profile_id)
        body = await _http_get(path)
        if isinstance(body, dict) and "__status" not in body:
            return
    logger.warning("dolphin stop_profile {} — все эндпойнты вернули ошибку", profile_id)


class DolphinSession:
    """Активная Dolphin Anty сессия: Playwright → Browser (CDP) → context.

    API совместим по основным методам с `app.bots.browser.BrowserSession`, чтобы
    использующий код (например `ElevenLabsBot`) мог принимать любой из двух.
    """

    def __init__(self, profile_id: str | None = None) -> None:
        self.profile_id = profile_id or settings.dolphin_profile_id
        if not self.profile_id:
            raise DolphinError(
                "DOLPHIN_PROFILE_ID не задан в .env (или в аргументе)"
            )
        if not settings.dolphin_api_token:
            raise DolphinError("DOLPHIN_API_TOKEN не задан в .env")
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._cdp_url: str | None = None

    async def start(self) -> None:
        assert self.profile_id  # для mypy/линтера
        self._cdp_url = await start_profile(self.profile_id)
        self._pw = await async_playwright().start()
        logger.info("dolphin: connecting playwright to {}", self._cdp_url)
        self.browser = await self._pw.chromium.connect_over_cdp(self._cdp_url)
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            self.context = await self.browser.new_context()
        logger.info(
            "dolphin: connected, contexts={}, pages={}",
            len(self.browser.contexts),
            len(self.context.pages),
        )

    async def stop(self) -> None:
        try:
            if self.browser is not None:
                await self.browser.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()
            # Маленькая пауза + просим Dolphin закрыть профиль.
            await asyncio.sleep(0.5)
            if self.profile_id:
                with contextlib.suppress(Exception):
                    await stop_profile(self.profile_id)

    async def open_page(self, url: str, *, reuse: bool = True) -> Page:
        assert self.context is not None
        if reuse:
            base = url.split("?", 1)[0]
            for p in self.context.pages:
                try:
                    if p.url and p.url.startswith(base):
                        with contextlib.suppress(Exception):
                            await p.bring_to_front()
                        return p
                except Exception:  # noqa: BLE001
                    continue
        page = await self.context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        return page


@contextlib.asynccontextmanager
async def dolphin_session(
    profile_id: str | None = None,
) -> AsyncIterator[DolphinSession]:
    """Контекст-менеджер: открывает Dolphin Anty профиль и отдаёт DolphinSession.
    При выходе закрывает профиль (если мы его стартовали)."""
    s = DolphinSession(profile_id)
    await s.start()
    try:
        yield s
    finally:
        await s.stop()
