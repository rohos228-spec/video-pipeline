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
    # confirmed: признак успеха найден; unconfirmed: кнопка нажата, итог не подтверждён;
    # failed: ошибка до или во время публикации.
    status: str = ""
    # clicked: клик Post/Publish был (или мог быть) выполнен. По нему решается, можно ли автоповтор.
    clicked: bool = False
    post_id: str | None = None
    error_code: str | None = None


class PublishGate:
    """Порт к журналу публикаций (app/services/publications_store). Нужен, чтобы
    publishers.py не знал про БД: намерение пишется ДО клика, итог сразу после платформы."""

    async def claim(self, platform: str) -> bool:  # взять платформу; False — не публиковать
        return True

    async def before_click(self, platform: str) -> bool:  # зафиксировать клик ДО клика
        return True

    async def record(self, result: "PublishResult") -> None:  # записать итог немедленно
        return None


class Publisher:
    platform: str = "base"
    upload_url: str = ""
    file_input_selectors: list[str] = ["input[type='file']"]
    caption_selectors: list[str] = ["textarea", "div[contenteditable='true']"]
    publish_button_selectors: list[str] = ["button:has-text('Post')", "button:has-text('Publish')"]
    # Признаки успешной публикации (селекторы тоста/страницы, фрагменты URL).
    # Пока не откалиброваны на живом UI: пустые списки дают только `unconfirmed`.
    success_selectors: list[str] = []
    success_url_markers: list[str] = []
    confirm_timeout_s: float = 8.0

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

    async def publish(self, video_path: Path, caption: str, *, before_click=None) -> PublishResult:
        """before_click: async-колбэк без аргументов; False/исключение — клик не делается.

        Всё, что случилось ДО клика, даёт failed с clicked=False (безопасно повторить).
        Любое исключение с момента вызова колбэка считается «клик мог состояться» (clicked=True).
        """
        clicked = False
        try:
            if not (caption or "").strip():
                return PublishResult(platform=self.platform, ok=False, status="failed",
                                     error="подпись пуста: публикация не начата", error_code="caption_empty")
            page = await self._open()

            file_sel = await self._first_visible(page, self.file_input_selectors, timeout_ms=30_000)
            if not file_sel:
                raise RuntimeError(f"{self.platform}: input[type=file] не найден")
            await page.locator(file_sel).first.set_input_files(str(video_path))

            # подпись: без неё пост не отправляем (failed ДО клика)
            cap_sel = await self._first_visible(page, self.caption_selectors, timeout_ms=30_000)
            if not cap_sel:
                return PublishResult(platform=self.platform, ok=False, status="failed",
                                     error=f"{self.platform}: поле подписи не найдено, клик не делался",
                                     error_code="caption_field_missing")
            try:
                await page.locator(cap_sel).first.click()
                await page.locator(cap_sel).first.fill(caption)
            except Exception as e:  # noqa: BLE001
                logger.warning("{}: не удалось заполнить caption", self.platform)
                return PublishResult(platform=self.platform, ok=False, status="failed",
                                     error=f"{self.platform}: подпись не заполнена ({e}), клик не делался",
                                     error_code="caption_fill_failed")

            # кнопка post / publish
            pub_sel = await self._first_visible(page, self.publish_button_selectors, timeout_ms=60_000)
            if not pub_sel:
                raise RuntimeError(f"{self.platform}: кнопка Publish не найдена")
            if before_click is not None:
                clicked = True  # если колбэк упадёт, лучше считать, что клик возможен
                if not await before_click():
                    return PublishResult(platform=self.platform, ok=False, status="failed",
                                         error=f"{self.platform}: клик не разрешён журналом публикаций",
                                         error_code="click_not_allowed", clicked=False)
            clicked = True
            await page.locator(pub_sel).first.click()

            if await self._confirm(page):
                if not page.url:  # confirmed без URL/ID невозможен
                    return PublishResult(platform=self.platform, ok=False, status="unconfirmed", clicked=True,
                                         error="unconfirmed: признак успеха есть, но URL поста пуст",
                                         error_code="confirmed_without_url")
                return PublishResult(platform=self.platform, ok=True, url=page.url, status="confirmed",
                                     clicked=True)
            logger.warning("{}: публикация не подтверждена признаком успеха", self.platform)
            return PublishResult(
                platform=self.platform, ok=False, url=page.url, status="unconfirmed", clicked=True,
                error="unconfirmed: кнопка нажата, признак успешной публикации не найден",
                error_code="no_success_marker",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("{}: publish failed", self.platform)
            return PublishResult(platform=self.platform, ok=False, error=str(e), status="failed",
                                 clicked=clicked, error_code="exception_after_click" if clicked else "exception_before_click")

    async def _confirm(self, page: Page) -> bool:
        """Ждём признак успеха; без признаков (или без совпадения) исход считается неподтверждённым."""
        if not self.success_selectors and not self.success_url_markers:
            await asyncio.sleep(self.confirm_timeout_s)
            return False
        deadline = asyncio.get_event_loop().time() + self.confirm_timeout_s
        while True:
            try:
                if any(m in (page.url or "") for m in self.success_url_markers):
                    return True
                for sel in self.success_selectors:
                    if await page.locator(sel).count() > 0:
                        return True
            except Exception:  # noqa: BLE001
                pass
            if asyncio.get_event_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.5)


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
    gate: PublishGate | None = None,
) -> list[PublishResult]:
    """Запускаем MoreLogin-профиль один раз и проходим по всем платформам.

    skip_platforms — не публиковать (уже есть итог). gate — журнал публикаций: claim до работы,
    before_click до клика, record сразу после каждой платформы (падение на следующей не теряет итог).
    """
    skip = skip_platforms or set()
    results: list[PublishResult] = []
    async with morelogin_browser() as browser:
        for cls in ALL_PUBLISHERS:
            pub = cls(browser)
            if pub.platform in skip:
                logger.info("skipping {} — уже опубликовано", pub.platform)
                continue
            if gate is not None and not await gate.claim(pub.platform):
                logger.info("skipping {} — платформа занята или ждёт человека (журнал публикаций)", pub.platform)
                continue
            logger.info("publishing → {}", pub.platform)

            async def _bc(_p=pub.platform):
                return await gate.before_click(_p)

            res = await pub.publish(video_path, caption, before_click=_bc if gate is not None else None)
            if gate is not None:
                await gate.record(res)
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
