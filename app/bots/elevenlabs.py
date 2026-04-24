"""11Labs (elevenlabs.io) — генерация озвучки через web-интерфейс
Speech Synthesis. Playwright + CDP (общая сессия с Chrome на хосте).

Модель предполагает, что пользователь уже зашёл в свой аккаунт в Chrome,
настроил голос и другие параметры по умолчанию. Бот только:
  1) открывает страницу /app/speech-synthesis,
  2) вбивает текст,
  3) жмёт Generate,
  4) ждёт, когда станет доступна кнопка Download,
  5) скачивает mp3.

⚠️ Как и outsee.io, конкретные селекторы будут подправлены после реального
запуска на твоей машине. Для разведки — `python -m app.bots.elevenlabs recon "тест"`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from loguru import logger
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PWTimeoutError

from app.bots.browser import BrowserSession, browser_session
from app.settings import settings

INPUT_SELECTORS = [
    "textarea[placeholder*='text' i]",
    "textarea[placeholder*='введите' i]",
    "textarea[name='text']",
    "div[contenteditable='true']",
    "textarea",
]
GENERATE_SELECTORS = [
    "button:has-text('Generate')",
    "button:has-text('Create')",
    "button[type='submit']",
    "button[data-testid='generate']",
]
DOWNLOAD_SELECTORS = [
    "a[download][href*='.mp3']",
    "button:has-text('Download')",
    "button[aria-label='Download']",
    "a[aria-label='Download']",
]


async def _first_visible(page: Page, selectors: list[str], *, timeout_ms: int = 20_000) -> str | None:
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                try:
                    if await loc.is_visible():
                        return sel
                except Exception:  # noqa: BLE001
                    continue
            except Exception:  # noqa: BLE001
                continue
        await asyncio.sleep(0.3)
    return None


class ElevenLabsBot:
    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    async def tts(self, text: str, out_path: Path, *, timeout: float = 300) -> Path:
        page = await self.session.open_page(settings.elevenlabs_web_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")

        input_sel = await _first_visible(page, INPUT_SELECTORS, timeout_ms=30_000)
        if not input_sel:
            raise RuntimeError("11Labs: не найден textarea для текста")
        await page.locator(input_sel).first.click()
        # Для длинных текстов используем fill (быстрее), для коротких тоже
        await page.locator(input_sel).first.fill(text)

        gen_sel = await _first_visible(page, GENERATE_SELECTORS, timeout_ms=10_000)
        if not gen_sel:
            raise RuntimeError("11Labs: не найдена кнопка Generate")
        await page.locator(gen_sel).first.click()

        # Ждём, когда появится ссылка Download — затем перехватываем событие download.
        deadline = asyncio.get_event_loop().time() + timeout
        out_path.parent.mkdir(parents=True, exist_ok=True)
        while asyncio.get_event_loop().time() < deadline:
            sel = await _first_visible(page, DOWNLOAD_SELECTORS, timeout_ms=2_000)
            if sel is None:
                await asyncio.sleep(1.0)
                continue
            try:
                async with page.expect_download(timeout=30_000) as dl_info:
                    await page.locator(sel).first.click()
                download = await dl_info.value
                await download.save_as(str(out_path))
                logger.info("11Labs mp3 saved → {}", out_path)
                return out_path
            except PWTimeoutError:
                # иногда <a download href> — не событие, а прямой GET
                href = await page.locator(sel).first.get_attribute("href")
                if href:
                    ctx = page.context
                    resp = await ctx.request.get(href)
                    if resp.status < 400:
                        out_path.write_bytes(await resp.body())
                        return out_path
                await asyncio.sleep(1.0)
                continue
        raise PWTimeoutError("11Labs: не дождались загрузки mp3")


async def _recon(text: str) -> None:
    async with browser_session() as bs:
        page = await bs.open_page(settings.elevenlabs_web_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)
        info = await page.evaluate(
            """() => {
                const q = sel => Array.from(document.querySelectorAll(sel)).slice(0, 10)
                    .map(el => ({
                        tag: el.tagName, id: el.id,
                        cls: el.className && el.className.toString().slice(0, 120),
                        placeholder: el.getAttribute('placeholder'),
                        ariaLabel: el.getAttribute('aria-label'),
                        text: (el.innerText || '').slice(0, 60),
                    }));
                return {
                    textareas: q('textarea'),
                    buttons: q('button'),
                    downloads: q("a[download], [href$='.mp3']"),
                };
            }"""
        )
        for name, items in info.items():
            logger.info("--- {} ({}) ---", name, len(items))
            for i, it in enumerate(items):
                logger.info("  [{}] {}", i, it)


def _cli() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "recon":
        print("usage: python -m app.bots.elevenlabs recon <text>")
        sys.exit(1)
    asyncio.run(_recon(sys.argv[2]))


if __name__ == "__main__":
    _cli()
