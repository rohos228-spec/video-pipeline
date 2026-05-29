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
from urllib.parse import urlparse

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.settings import settings

# Сообщения playwright, по которым мы понимаем что Chrome/контекст «умер»
# и нужно переподключиться (юзер закрыл вкладку/перезапустил браузер,
# CDP-соединение разорвалось и т.д.).
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


def url_base_for_reuse(url: str) -> str:
    """Нормализованный host+path для сравнения вкладок (без query/hash/www)."""
    if not url:
        return ""
    bare = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    parsed = urlparse(bare)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    scheme = (parsed.scheme or "https").lower()
    return f"{scheme}://{host}{path}"


def page_url_matches_target(page_url: str, target_url: str) -> bool:
    """True если вкладка уже на том же сайте/разделе, что и target URL."""
    page_base = url_base_for_reuse(page_url)
    target_base = url_base_for_reuse(target_url)
    if not page_base or not target_base:
        return False
    if page_base == target_base:
        return True
    return page_base.startswith(f"{target_base}/") or target_base.startswith(
        f"{page_base}/"
    )


class BrowserSession:
    """Живая сессия: Playwright → Browser (CDP) → первый существующий context."""

    def __init__(self) -> None:
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.force_new_window: bool = False

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

    async def reconnect(self) -> None:
        """Переподключиться к Chrome через CDP.

        Используется в случае, когда юзер закрыл chrome-окно, перезапустил
        браузер или playwright-соединение порвалось. Без этого любые
        последующие операции на старом `context` падают с TargetClosedError
        (и кнопки в TG висят в «вечной загрузке» до timeout'а).
        """
        logger.warning("browser: reconnecting to chrome over cdp...")
        # Пытаемся аккуратно закрыть старое соединение, но если уже мёртво —
        # не блокируемся.
        old_browser = self.browser
        self.browser = None
        self.context = None
        if old_browser is not None:
            try:  # noqa: SIM105
                await old_browser.close()
            except Exception:  # noqa: BLE001
                pass
        # `_pw` оставляем тот же — повторный async_playwright().start() в том
        # же event-loop'е не нужен; если вдруг и playwright «упал», создадим
        # его заново.
        if self._pw is None:
            self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.connect_over_cdp(
            settings.browser_cdp_url
        )
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            self.context = await self.browser.new_context()
        logger.info(
            "browser: reconnected, contexts={}, pages={}",
            len(self.browser.contexts),
            len(self.context.pages),
        )

    async def open_page(self, url: str, *, reuse: bool = True) -> Page:
        """Открыть вкладку. Если reuse=True и уже есть вкладка с тем же URL-префиксом —
        возвращаем её.

        На TargetClosedError (юзер закрыл chrome / браузер упал) делаем один
        reconnect и повторяем — это лечит «вечную загрузку» кнопок в TG, когда
        долгий шаг (5/6) подвисал в playwright потому что контекст уже мёртв.
        """
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
        # сюда мы не попадаем — либо вернули page, либо raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("open_page: unexpected loop exit")

    async def _open_page_once(self, url: str, *, reuse: bool = True) -> Page:
        assert self.context is not None
        if self.force_new_window:
            reuse = False
        if reuse:
            target: Page | None = None
            for p in self.context.pages:
                try:
                    if p.url and page_url_matches_target(p.url, url):
                        target = p
                        break
                except Exception:  # noqa: BLE001
                    continue
            if target is not None:
                logger.info(
                    "browser.open_page: reuse existing tab url={} for target={}",
                    target.url,
                    url,
                )
                try:  # noqa: SIM105
                    await target.bring_to_front()
                except Exception:  # noqa: BLE001
                    pass
                return target
        logger.info("browser.open_page: opening new tab for {}", url)
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
