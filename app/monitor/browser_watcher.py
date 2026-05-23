"""Browser Watcher — подключается к Chrome через CDP и в фоне:
  1. Делает скриншоты всех открытых вкладок каждые N секунд.
  2. Записывает консольные сообщения и сетевые ошибки.
  3. Фиксирует навигации (URL-изменения).

Работает ПАРАЛЛЕЛЬНО с основным ботом — подключается к тому же Chrome
через CDP (read-only наблюдатель, не мешает Playwright-автоматизации).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from app.monitor.log_sink import emit_event, get_monitor_dir


class BrowserWatcher:
    """Наблюдатель за Chrome через CDP — скриншоты + события."""

    def __init__(
        self,
        cdp_url: str = "http://localhost:29229",
        *,
        screenshot_interval: float = 10.0,
        screenshot_on_change: bool = True,
    ) -> None:
        self.cdp_url = cdp_url
        self.screenshot_interval = screenshot_interval
        self.screenshot_on_change = screenshot_on_change

        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._running = False
        self._task: asyncio.Task | None = None

        self._screenshots_dir: Path | None = None
        self._last_urls: dict[str, str] = {}
        self._page_hooks: set[str] = set()

    async def start(self) -> None:
        """Подключается к Chrome и запускает фоновый цикл."""
        self._screenshots_dir = get_monitor_dir() / "screenshots"
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(
                self.cdp_url, timeout=10_000
            )
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
            else:
                self._context = await self._browser.new_context()

            logger.info(
                "browser_watcher: подключён к Chrome ({}), {} вкладок",
                self.cdp_url,
                len(self._context.pages),
            )
            emit_event(
                "watcher_connected",
                detail={
                    "cdp_url": self.cdp_url,
                    "tabs": len(self._context.pages),
                },
            )

            self._running = True
            self._task = asyncio.create_task(self._loop())

        except Exception as e:
            logger.warning(
                "browser_watcher: не удалось подключиться к Chrome ({}): {}",
                self.cdp_url, e,
            )
            emit_event(
                "watcher_connect_failed",
                detail={"cdp_url": self.cdp_url, "error": str(e)},
            )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
        logger.info("browser_watcher: остановлен")

    async def _loop(self) -> None:
        """Основной цикл: снимаем скриншоты + слушаем события."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("browser_watcher tick error: {}", e)
                emit_event(
                    "watcher_error",
                    detail={"error": str(e)},
                )
            await asyncio.sleep(self.screenshot_interval)

    async def _tick(self) -> None:
        """Один тик: скриншоты всех вкладок + регистрация хуков."""
        if self._context is None:
            return

        pages = self._context.pages
        if not pages:
            return

        for i, page in enumerate(pages):
            page_id = f"tab_{i}"
            try:
                url = page.url or ""
            except Exception:
                continue

            self._hook_page_events(page, page_id)

            old_url = self._last_urls.get(page_id)
            url_changed = old_url != url
            if url_changed:
                self._last_urls[page_id] = url
                emit_event(
                    "navigation",
                    detail={"tab": page_id, "url": url, "prev_url": old_url},
                )

            if url_changed or not self.screenshot_on_change:
                await self._take_screenshot(page, page_id, url)

    async def take_screenshot_now(
        self, label: str = "manual"
    ) -> list[str]:
        """Немедленный снимок всех вкладок. Возвращает пути к файлам."""
        paths: list[str] = []
        if self._context is None:
            return paths
        for i, page in enumerate(self._context.pages):
            page_id = f"tab_{i}"
            url = ""
            try:
                url = page.url or ""
            except Exception:
                pass
            p = await self._take_screenshot(page, page_id, url, label=label)
            if p:
                paths.append(p)
        return paths

    async def _take_screenshot(
        self,
        page: Page,
        page_id: str,
        url: str,
        *,
        label: str = "",
    ) -> str | None:
        if self._screenshots_dir is None:
            return None
        ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        suffix = f"_{label}" if label else ""
        fname = f"{ts}_{page_id}{suffix}.png"
        fpath = self._screenshots_dir / fname

        try:
            await page.screenshot(path=str(fpath), timeout=5000)
            emit_event(
                "screenshot",
                screenshot_path=str(fpath),
                detail={
                    "tab": page_id,
                    "url": url[:200],
                    "file": fname,
                },
            )
            return str(fpath)
        except Exception as e:
            logger.debug(
                "browser_watcher: скриншот {} не удался: {}", page_id, e
            )
            return None

    def _hook_page_events(self, page: Page, page_id: str) -> None:
        """Навешивает one-time слушатели на страницу."""
        key = f"{id(page)}_{page_id}"
        if key in self._page_hooks:
            return
        self._page_hooks.add(key)

        def on_console(msg):
            level = msg.type
            if level in ("error", "warning"):
                emit_event(
                    "console",
                    detail={
                        "tab": page_id,
                        "level": level,
                        "text": msg.text[:500],
                    },
                )

        def on_pageerror(error):
            emit_event(
                "page_error",
                detail={
                    "tab": page_id,
                    "error": str(error)[:500],
                },
            )

        def on_request_failed(request):
            emit_event(
                "request_failed",
                detail={
                    "tab": page_id,
                    "url": request.url[:300],
                    "method": request.method,
                    "failure": str(request.failure)[:200] if request.failure else "",
                },
            )

        def on_response(response):
            status = response.status
            if status >= 400:
                emit_event(
                    "http_error",
                    detail={
                        "tab": page_id,
                        "url": response.url[:300],
                        "status": status,
                    },
                )

        try:
            page.on("console", on_console)
            page.on("pageerror", on_pageerror)
            page.on("requestfailed", on_request_failed)
            page.on("response", on_response)
        except Exception as e:
            logger.debug("browser_watcher: hook failed for {}: {}", page_id, e)

    def get_page_urls(self) -> dict[str, str]:
        """Текущие URL всех отслеживаемых вкладок."""
        return dict(self._last_urls)
