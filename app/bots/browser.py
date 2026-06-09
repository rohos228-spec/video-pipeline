"""Playwright: CDP к Chrome пользователя (ChatGPT, Outsee, 11Labs).

Запуск Chrome (Windows): Start-Chrome.cmd или scripts\\Start-ChromeCDP.ps1
Профиль: %USERPROFILE%\\.vp_browser_data  Порт: 29229

Не запускайте «обычный» Chrome без --remote-debugging-port и без user-data-dir —
/json/version может отвечать, а connect_over_cdp зависнет после ws connected.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.bots import chrome_cdp as cdp
from app.settings import settings

_DEAD_BROWSER_MARKERS = (
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "context has been closed",
    "browser closed",
    "connection closed",
    "websocket: close",
    "no target",
)


def _looks_like_dead_browser(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _DEAD_BROWSER_MARKERS)


def _looks_like_cdp_connect_failure(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return "connect_over_cdp" in msg or (
        "timeout" in msg and ("exceeded" in msg or "180000" in msg)
    )


class BrowserSession:
    """Playwright → Browser (CDP) → первый default context."""

    def __init__(self) -> None:
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.force_new_window: bool = False

    async def _attach_cdp_context(self) -> None:
        assert self.browser is not None
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            self.context = await self.browser.new_context()
        n_pages = len(self.context.pages) if self.context else 0
        logger.info(
            "browser connected, contexts={}, pages={}",
            len(self.browser.contexts),
            n_pages,
        )

    async def _connect_over_cdp_once(self, url: str, *, timeout_ms: int) -> None:
        assert self._pw is not None
        self.browser = await self._pw.chromium.connect_over_cdp(url, timeout=timeout_ms)
        await self._attach_cdp_context()

    async def _connect_over_cdp(self) -> None:
        assert self._pw is not None
        url = cdp.normalize_cdp_http_url(settings.browser_cdp_url)
        timeout_ms = settings.browser_cdp_connect_timeout_ms
        chrome_restarted = False
        last_err: BaseException | None = None

        async with cdp._CDP_LOCK:
            while True:
                await cdp.log_cdp_health(url)
                last_err = None
                for attempt in range(1, 3):
                    logger.info(
                        "connecting to chrome over cdp at {} "
                        "(attempt {}/2, timeout={}ms, restarted={})",
                        url,
                        attempt,
                        timeout_ms,
                        chrome_restarted,
                    )
                    try:
                        await self._connect_over_cdp_once(url, timeout_ms=timeout_ms)
                        return
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        self.browser = None
                        self.context = None
                        logger.warning(
                            "connect_over_cdp attempt {}/2 failed: {}",
                            attempt,
                            e,
                        )
                        if attempt < 2:
                            await asyncio.sleep(1.5)

                if (
                    not chrome_restarted
                    and last_err is not None
                    and cdp.playwright_cdp_hang(last_err)
                    and await cdp.recover_chrome_cdp()
                ):
                    chrome_restarted = True
                    url = cdp.normalize_cdp_http_url(settings.browser_cdp_url)
                    await asyncio.sleep(2)
                    continue

                if last_err is not None:
                    raise RuntimeError(
                        "Не удалось подключиться к Chrome CDP. "
                        "Закройте все окна Chrome и запустите Start-Chrome.cmd "
                        f"(профиль .vp_browser_data, порт {cdp.cdp_port_from_url(url)}). "
                        f"Причина: {last_err}"
                    ) from last_err
                raise RuntimeError("connect_over_cdp: unknown failure")

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        await self._connect_over_cdp()

    async def stop(self) -> None:
        try:
            if self.browser is not None:
                await self.browser.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()
            self.browser = None
            self.context = None
            self._pw = None

    async def reconnect(self) -> None:
        logger.warning("browser: reconnecting to chrome over cdp...")
        old_browser = self.browser
        self.browser = None
        self.context = None
        if old_browser is not None:
            try:  # noqa: SIM105
                await old_browser.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw is None:
            self._pw = await async_playwright().start()
        await self._connect_over_cdp()

    async def open_page(self, url: str, *, reuse: bool = True) -> Page:
        last_exc: BaseException | None = None
        for attempt in (0, 1):
            try:
                return await self._open_page_once(url, reuse=reuse)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt == 0 and _looks_like_dead_browser(e):
                    logger.warning(
                        "browser.open_page: chrome-контекст мёртв ({}). "
                        "Переподключаюсь и пробую ещё раз.",
                        type(e).__name__,
                    )
                    try:
                        await self.reconnect()
                    except Exception as rc_e:  # noqa: BLE001
                        logger.error(
                            "browser.open_page: reconnect провалился: {}", rc_e
                        )
                        raise
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("open_page: unexpected loop exit")

    async def _open_page_once(self, url: str, *, reuse: bool = True) -> Page:
        assert self.context is not None
        if self.force_new_window:
            reuse = False
        if reuse:
            target = None
            prefix = url.split("?", 1)[0]
            for p in self.context.pages:
                try:
                    if p.url and p.url.startswith(prefix):
                        target = p
                        break
                except Exception:  # noqa: BLE001
                    continue
            if target is not None:
                try:  # noqa: SIM105
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


async def wait_for_selector_stable(
    page: Page,
    selector: str,
    *,
    settle_ms: int = 1500,
    timeout_ms: int = 120_000,
) -> None:
    await page.wait_for_selector(selector, timeout=timeout_ms)
    last_sig = None
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    stable_since: float | None = None
    while loop.time() < deadline:
        try:
            box = await page.locator(selector).bounding_box()
            text = await page.locator(selector).inner_text()
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.25)
            continue
        sig = (
            round(box["width"], 1) if box else None,
            round(box["height"], 1) if box else None,
            len(text),
        )
        if sig == last_sig:
            if stable_since is None:
                stable_since = loop.time()
            elif (loop.time() - stable_since) * 1000 >= settle_ms:
                return
        else:
            last_sig = sig
            stable_since = None
        await asyncio.sleep(0.25)
    raise TimeoutError(f"selector {selector} did not stabilise in {timeout_ms} ms")
