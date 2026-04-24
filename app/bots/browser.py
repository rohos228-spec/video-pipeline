"""Playwright-обёртка: подключение по CDP к уже запущенному Chrome (локально
на ПК пользователя), получение готовой страницы по URL и базовые helpers.

Chrome запускается пользователем один раз с флагами (см. HOW_TO_RUN.md):
  --remote-debugging-port=29229
  --user-data-dir=%USERPROFILE%\\.vp_browser_data  (Windows)

В этом Chrome пользователь залогинен в ChatGPT, outsee.io, 11Labs и т.д. —
Playwright подхватывает все залогиненные контексты.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.settings import settings


class BrowserSession:
    """Живая сессия: Playwright → Browser (CDP) → первый существующий context."""

    def __init__(self) -> None:
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        logger.info("connecting to chrome over cdp at {}", settings.browser_cdp_url)
        self.browser = await self._pw.chromium.connect_over_cdp(settings.browser_cdp_url)
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            self.context = await self.browser.new_context()
        logger.info("browser connected, contexts={}, pages={}",
                    len(self.browser.contexts), len(self.context.pages))

    async def stop(self) -> None:
        try:
            if self.browser is not None:
                await self.browser.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()

    async def open_page(self, url: str, *, reuse: bool = True) -> Page:
        """Открыть вкладку. Если reuse=True и уже есть вкладка с тем же URL-префиксом —
        возвращаем её."""
        assert self.context is not None
        if reuse:
            target = None
            for p in self.context.pages:
                try:
                    if p.url and p.url.startswith(url.split("?", 1)[0]):
                        target = p
                        break
                except Exception:  # noqa: BLE001
                    continue
            if target is not None:
                try:
                    await target.bring_to_front()
                except Exception:  # noqa: BLE001
                    pass
                return target
        page = await self.context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        return page


@asynccontextmanager
async def browser_session() -> AsyncIterator[BrowserSession]:
    session = BrowserSession()
    await session.start()
    try:
        yield session
    finally:
        await session.stop()


async def wait_for_selector_stable(page: Page, selector: str, *, settle_ms: int = 1500,
                                   timeout_ms: int = 120_000) -> None:
    """Ждём, пока узел появился и перестал меняться по size/text в течение `settle_ms`."""
    await page.wait_for_selector(selector, timeout=timeout_ms)
    last_sig = None
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    stable_since: float | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            box = await page.locator(selector).bounding_box()
            text = await page.locator(selector).inner_text()
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.25)
            continue
        sig = (round(box["width"], 1) if box else None,
               round(box["height"], 1) if box else None,
               len(text))
        if sig == last_sig:
            if stable_since is None:
                stable_since = asyncio.get_event_loop().time()
            elif (asyncio.get_event_loop().time() - stable_since) * 1000 >= settle_ms:
                return
        else:
            last_sig = sig
            stable_since = None
        await asyncio.sleep(0.25)
    raise TimeoutError(f"selector {selector} did not stabilise in {timeout_ms} ms")
