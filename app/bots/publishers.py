"""Публикация готового mp4 на 5 платформ через один MoreLogin-профиль.

Общая стратегия: открываем страницу загрузки каждой платформы, загружаем файл
через `input[type=file]`, вбиваем подпись (caption), жмём Publish. Селекторы
отличаются, но структура одинаковая — поэтому есть базовый класс Publisher.

⚠️ Селекторы каждой платформы — кандидаты, **не верифицированы** на живом UI.
Для калибровки есть режим разведки: `python -m app.bots.publishers recon tiktok`.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from playwright.async_api import Browser, Page

from app.bots.morelogin import morelogin_browser


@dataclass
class PublishResult:
    platform: str
    ok: bool
    url: str | None = None
    error: str | None = None


class Publisher:
    platform: str = "base"
    upload_url: str = ""
    file_input_selectors: list[str] = ["input[type='file']"]
    caption_selectors: list[str] = ["textarea", "div[contenteditable='true']"]
    publish_button_selectors: list[str] = ["button:has-text('Post')", "button:has-text('Publish')"]

    def __init__(self, browser: Browser) -> None:
        self.browser = browser

    async def _open(self) -> Page:
        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        # Переиспользуем открытую вкладку той же платформы, если есть.
        for p in ctx.pages:
            try:
                if p.url and any(host in p.url for host in self._hosts()):
                    await p.goto(self.upload_url, wait_until="domcontentloaded")
                    return p
            except Exception:  # noqa: BLE001
                continue
        page = await ctx.new_page()
        await page.goto(self.upload_url, wait_until="domcontentloaded")
        return page

    def _hosts(self) -> list[str]:
        from urllib.parse import urlparse
        host = urlparse(self.upload_url).netloc
        return [host]

    async def _first_visible(self, page: Page, selectors: list[str], *, timeout_ms: int = 20_000) -> str | None:
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            for sel in selectors:
                try:
                    if await page.locator(sel).count() > 0:
                        return sel
                except Exception:  # noqa: BLE001
                    continue
            await asyncio.sleep(0.3)
        return None

    async def publish(self, video_path: Path, caption: str) -> PublishResult:
        try:
            page = await self._open()

            file_sel = await self._first_visible(page, self.file_input_selectors, timeout_ms=30_000)
            if not file_sel:
                raise RuntimeError(f"{self.platform}: input[type=file] не найден")
            await page.locator(file_sel).first.set_input_files(str(video_path))

            # подпись
            cap_sel = await self._first_visible(page, self.caption_selectors, timeout_ms=30_000)
            if cap_sel:
                try:
                    await page.locator(cap_sel).first.click()
                    await page.locator(cap_sel).first.fill(caption)
                except Exception:  # noqa: BLE001
                    logger.warning("{}: не удалось заполнить caption", self.platform)

            # кнопка post / publish
            pub_sel = await self._first_visible(page, self.publish_button_selectors, timeout_ms=60_000)
            if not pub_sel:
                raise RuntimeError(f"{self.platform}: кнопка Publish не найдена")
            await page.locator(pub_sel).first.click()

            # Ждём какое-то время — UI обычно делает редирект или показывает тост «опубликовано»
            await asyncio.sleep(8)
            return PublishResult(platform=self.platform, ok=True, url=page.url)
        except Exception as e:  # noqa: BLE001
            logger.exception("{}: publish failed", self.platform)
            return PublishResult(platform=self.platform, ok=False, error=str(e))


class TikTokPublisher(Publisher):
    platform = "tiktok"
    upload_url = "https://www.tiktok.com/tiktokstudio/upload"
    caption_selectors = [
        "div[contenteditable='true'][data-placeholder*='каптион' i]",
        "div[contenteditable='true']",
        "textarea",
    ]
    publish_button_selectors = [
        "button:has-text('Post')",
        "button:has-text('Опубликовать')",
    ]


class YouTubeShortsPublisher(Publisher):
    platform = "yt_shorts"
    upload_url = "https://studio.youtube.com/channel/UC/videos/upload"
    caption_selectors = [
        "ytcp-social-suggestions-textbox div[contenteditable='true']",
        "div[contenteditable='true']",
    ]
    publish_button_selectors = [
        "ytcp-button#done-button button",
        "button:has-text('Опубликовать')",
        "button:has-text('Publish')",
    ]


class InstagramReelsPublisher(Publisher):
    platform = "ig_reels"
    # instagram web требует дополнительного клика "Create → Post/Reel" перед
    # появлением файлового input; в базовом сценарии пробуем прямой URL.
    upload_url = "https://www.instagram.com/"
    file_input_selectors = ["input[type='file'][accept*='video']"]
    caption_selectors = ["textarea[aria-label*='caption' i]", "textarea"]
    publish_button_selectors = ["button:has-text('Share')", "div[role='button']:has-text('Share')"]


class VKPublisher(Publisher):
    platform = "vk_clips"
    upload_url = "https://vk.com/clips"
    caption_selectors = ["textarea[name='caption']", "textarea"]
    publish_button_selectors = [
        "button:has-text('Опубликовать')",
        "button:has-text('Публиковать')",
        "button:has-text('Post')",
    ]


class LikeePublisher(Publisher):
    platform = "likee"
    upload_url = "https://likee.video/upload"
    caption_selectors = ["textarea", "div[contenteditable='true']"]
    publish_button_selectors = ["button:has-text('Publish')", "button:has-text('Опубликовать')"]


ALL_PUBLISHERS: list[type[Publisher]] = [
    TikTokPublisher,
    YouTubeShortsPublisher,
    InstagramReelsPublisher,
    VKPublisher,
    LikeePublisher,
]


async def publish_everywhere(
    video_path: Path,
    caption: str,
    *,
    skip_platforms: set[str] | None = None,
) -> list[PublishResult]:
    """Запускаем MoreLogin-профиль один раз и проходим по всем платформам.

    Если `skip_platforms` задан — платформы из него пропускаются (для retry-логики,
    чтобы не публиковать дважды туда, где уже успешно выложили).
    """
    skip = skip_platforms or set()
    results: list[PublishResult] = []
    async with morelogin_browser() as browser:
        for cls in ALL_PUBLISHERS:
            pub = cls(browser)
            if pub.platform in skip:
                logger.info("skipping {} — уже опубликовано", pub.platform)
                continue
            logger.info("publishing → {}", pub.platform)
            res = await pub.publish(video_path, caption)
            results.append(res)
            # небольшая пауза между платформами
            await asyncio.sleep(3)
    return results


async def _recon(platform: str) -> None:
    from urllib.parse import urlparse
    cls = next((c for c in ALL_PUBLISHERS if c.platform == platform), None)
    if cls is None:
        print("unknown platform. choose one of:", ", ".join(c.platform for c in ALL_PUBLISHERS))
        sys.exit(1)
    async with morelogin_browser() as browser:
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto(cls.upload_url, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        info = await page.evaluate(
            """() => {
                const q = sel => Array.from(document.querySelectorAll(sel)).slice(0, 12)
                    .map(el => ({
                        tag: el.tagName, id: el.id,
                        cls: el.className && el.className.toString().slice(0, 120),
                        ariaLabel: el.getAttribute('aria-label'),
                        placeholder: el.getAttribute('placeholder'),
                        text: (el.innerText || '').slice(0, 80),
                    }));
                return {
                    fileInputs: q("input[type='file']"),
                    textareas: q('textarea'),
                    contenteditables: q("[contenteditable='true']"),
                    buttons: q('button'),
                };
            }"""
        )
        for k, v in info.items():
            logger.info("--- {} ({}) ---", k, len(v))
            for i, x in enumerate(v):
                logger.info("  [{}] {}", i, x)
        _host = urlparse(page.url).netloc
        logger.info("done recon for {}", _host)


def _cli() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "recon":
        print("usage: python -m app.bots.publishers recon <tiktok|yt_shorts|ig_reels|vk_clips|likee>")
        sys.exit(1)
    asyncio.run(_recon(sys.argv[2]))


if __name__ == "__main__":
    _cli()
