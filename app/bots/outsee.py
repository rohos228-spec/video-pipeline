"""outsee.io — генерация картинок (nano-banana-2) и видео (veo-3-fast Relax)
через автоматизацию браузера (Playwright поверх CDP).

⚠️ Селекторы UI outsee.io не задокументированы публично. На момент написания
этого файла мы ещё не провели DOM-рекон. Поэтому здесь скелет с несколькими
разумными кандидатами селекторов; при первом запуске их почти наверняка
придётся скорректировать, увидев реальный DOM. Для этого есть режим разведки:
    python -m app.bots.outsee recon-image "тестовый промт"
    python -m app.bots.outsee recon-video "тестовый промт" /путь/к/картинке.png

Он откроет страницу, подождёт, и выведет в лог подходящие элементы
(textarea, button, input[type=file]) с их селекторами — чтобы быстро их
закрепить в этом файле.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PWTimeoutError

from app.bots.browser import BrowserSession, browser_session
from app.settings import settings

# Порядок попыток — первый сработавший используется
PROMPT_INPUT_SELECTORS = [
    "textarea[placeholder*='prompt' i]",
    "textarea[placeholder*='промпт' i]",
    "textarea[placeholder*='промт' i]",
    "textarea[placeholder*='опис' i]",
    "textarea[name='prompt']",
    "textarea[data-testid='prompt']",
    "textarea",
    "div[contenteditable='true']",
]

GENERATE_BUTTON_SELECTORS = [
    "button:has-text('Генерировать')",
    "button:has-text('Генерация')",
    "button:has-text('Сгенерировать')",
    "button:has-text('Создать')",
    "button:has-text('Generate')",
    "button:has-text('Run')",
    "button[data-testid='generate']",
    "button[type='submit']",
]

ASPECT_9_16_SELECTORS = [
    "button:has-text('9:16')",
    "[data-value='9:16']",
    "[aria-label='9:16']",
    "input[value='9:16']",
    # На outsee «Соотношение» отображается как кликабельный блок, внутри
    # которого текст «9:16». Ищем родителя через :has().
    "*:has(> :text-is('9:16'))",
]

FILE_UPLOAD_SELECTORS = [
    "input[type='file']",
]

# Селекторы для скачивания результата. Покрываем два случая:
#   - result картинка отображается как <img src=...>
#   - видео отображается как <video src=...> или есть кнопка download
RESULT_IMAGE_SELECTORS = [
    "img[data-testid='result']",
    "img[alt*='result' i]",
    "[role='img'] img",
    "main img",
]
RESULT_VIDEO_SELECTORS = [
    "video source",
    "video",
    "a[download][href*='.mp4']",
]
DOWNLOAD_BUTTON_SELECTORS = [
    "button[aria-label='Download']",
    "button:has-text('Download')",
    "button:has-text('Скачать')",
    "a[download]",
]


@dataclass
class GenerationResult:
    """Итог генерации."""
    file_path: Path
    raw_url: str | None = None


async def _first_visible(
    page: Page, selectors: list[str], *, timeout_ms: int = 15_000
) -> str | None:
    """Возвращает CSS-селектор с уже вставленным `:nth-match(sel, N)`, который
    гарантированно попадает в первый ВИДИМЫЙ элемент. Страницы outsee часто
    рендерят 2–3 копии одного textarea (desktop + mobile + sidebar), и
    locator(sel).first может ткнуть в скрытую."""
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        for sel in selectors:
            try:
                base = page.locator(sel)
                count = await base.count()
                if count == 0:
                    continue
                for i in range(min(count, 8)):
                    loc = base.nth(i)
                    try:
                        if await loc.is_visible():
                            # Вернём Playwright-селектор `:nth-match(X, N+1)`
                            return f":nth-match({sel}, {i + 1})"
                    except Exception:  # noqa: BLE001
                        # input[type=file] обычно hidden, но валиден
                        if "file" in sel:
                            return f":nth-match({sel}, {i + 1})"
                        continue
            except Exception:  # noqa: BLE001
                continue
        await asyncio.sleep(0.3)
    return None


class OutseeBot:
    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    # ----- IMAGE (nano-banana-2) -----

    async def generate_image(
        self,
        prompt: str,
        out_path: Path,
        *,
        aspect_ratio: str = "9:16",
        timeout: float = 300,
    ) -> GenerationResult:
        page = await self.session.open_page(settings.outsee_image_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        # Next.js-страница outsee гидратится дольше 3 сек — даём ей доразложиться.
        await page.wait_for_load_state("networkidle", timeout=30_000)

        # 1) вбить промт
        input_sel = await _first_visible(
            page, PROMPT_INPUT_SELECTORS, timeout_ms=60_000
        )
        if not input_sel:
            raise RuntimeError(
                "outsee image: не найден ввод промта "
                "(обнови селекторы в app/bots/outsee.py)"
            )
        # Страница длинная — прокручиваем к полю, иначе click промахивается.
        try:
            await page.locator(input_sel).first.scroll_into_view_if_needed(
                timeout=5_000
            )
        except Exception:  # noqa: BLE001
            pass
        await page.locator(input_sel).first.click()
        await page.locator(input_sel).first.fill(prompt)

        # 2) выбрать 9:16 (best-effort — если кнопки нет, считаем, что уже выбрано)
        if aspect_ratio == "9:16":
            ar_sel = await _first_visible(page, ASPECT_9_16_SELECTORS, timeout_ms=4_000)
            if ar_sel:
                try:
                    await page.locator(ar_sel).first.click()
                except Exception:  # noqa: BLE001
                    logger.warning("не удалось кликнуть по селектору 9:16 ({})", ar_sel)

        # 3) кнопка generate
        gen_sel = await _first_visible(page, GENERATE_BUTTON_SELECTORS, timeout_ms=10_000)
        if not gen_sel:
            raise RuntimeError("outsee image: не найдена кнопка Generate")
        await page.locator(gen_sel).first.click()

        # 4) ждём появления <img> с результатом
        img_url = await self._wait_image_url(page, timeout=timeout)

        # 5) скачиваем
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await _download_via_context(page, img_url, out_path)
        logger.info("outsee image saved → {}", out_path)
        return GenerationResult(file_path=out_path, raw_url=img_url)

    async def _wait_image_url(self, page: Page, *, timeout: float) -> str:
        deadline = asyncio.get_event_loop().time() + timeout
        last_known: set[str] = {
            src for src in await page.evaluate(
                "() => Array.from(document.querySelectorAll('img')).map(i => i.src)"
            )
            if src
        }
        while asyncio.get_event_loop().time() < deadline:
            now = {
                src for src in await page.evaluate(
                    "() => Array.from(document.querySelectorAll('img')).map(i => i.src)"
                )
                if src
            }
            new = now - last_known
            # Берём свежий src, который выглядит как сгенерированная картинка
            for u in new:
                if any(tok in u for tok in ("blob:", "outsee", "cdn", "storage", ".png", ".jpg", ".webp")):
                    return u
            await asyncio.sleep(1.0)
        raise PWTimeoutError("outsee image: результат не появился за отведённое время")

    # ----- VIDEO (veo-3-fast Relax) -----

    async def generate_video(
        self,
        prompt: str,
        out_path: Path,
        *,
        start_frame: Path | None = None,
        aspect_ratio: str = "9:16",
        timeout: float = 900,
    ) -> GenerationResult:
        page = await self.session.open_page(settings.outsee_video_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=30_000)

        # 1) ввод промта
        input_sel = await _first_visible(
            page, PROMPT_INPUT_SELECTORS, timeout_ms=60_000
        )
        if not input_sel:
            raise RuntimeError("outsee video: не найден ввод промта")
        try:
            await page.locator(input_sel).first.scroll_into_view_if_needed(
                timeout=5_000
            )
        except Exception:  # noqa: BLE001
            pass
        await page.locator(input_sel).first.click()
        await page.locator(input_sel).first.fill(prompt)

        # 2) аспект
        if aspect_ratio == "9:16":
            ar_sel = await _first_visible(page, ASPECT_9_16_SELECTORS, timeout_ms=4_000)
            if ar_sel:
                try:
                    await page.locator(ar_sel).first.click()
                except Exception:  # noqa: BLE001
                    pass

        # 3) загрузка стартового кадра (если передан)
        if start_frame is not None:
            file_sel = await _first_visible(page, FILE_UPLOAD_SELECTORS, timeout_ms=10_000)
            if not file_sel:
                raise RuntimeError("outsee video: не найден input[type=file] для стартового кадра")
            await page.locator(file_sel).first.set_input_files(str(start_frame))

        # 4) generate
        gen_sel = await _first_visible(page, GENERATE_BUTTON_SELECTORS, timeout_ms=10_000)
        if not gen_sel:
            raise RuntimeError("outsee video: не найдена кнопка Generate")
        await page.locator(gen_sel).first.click()

        # 5) ждём результат
        video_url = await self._wait_video_url(page, timeout=timeout)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        await _download_via_context(page, video_url, out_path)
        logger.info("outsee video saved → {}", out_path)
        return GenerationResult(file_path=out_path, raw_url=video_url)

    async def _wait_video_url(self, page: Page, *, timeout: float) -> str:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            urls = await page.evaluate(
                """() => {
                    const list = [];
                    document.querySelectorAll('video').forEach(v => {
                        if (v.src) list.push(v.src);
                        v.querySelectorAll('source').forEach(s => s.src && list.push(s.src));
                    });
                    document.querySelectorAll("a[download]").forEach(a => a.href && list.push(a.href));
                    return list;
                }"""
            )
            for u in urls:
                if any(tok in u for tok in (".mp4", "blob:", "video", "cdn", "storage")):
                    return u
            await asyncio.sleep(1.5)
        raise PWTimeoutError("outsee video: результат не появился за отведённое время")


async def _download_via_context(page: Page, url: str, out_path: Path) -> None:
    """Скачивает файл по URL, используя тот же контекст (cookies/auth) страницы."""
    # Playwright APIRequestContext унаследует cookies из контекста.
    ctx = page.context
    api = ctx.request
    resp = await api.get(url)
    if resp.status >= 400:
        raise RuntimeError(f"download {url} failed: HTTP {resp.status}")
    body = await resp.body()
    out_path.write_bytes(body)


# ---------- recon util: python -m app.bots.outsee recon-image "prompt" ----------

async def _recon(kind: str, prompt: str, start_frame: str | None = None) -> None:
    url = settings.outsee_image_url if kind == "image" else settings.outsee_video_url
    async with browser_session() as bs:
        page = await bs.open_page(url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        # Ждём окончания сетевой активности (Next.js гидратация).
        try:
            await page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(5)
        # Печатаем найденные элементы для калибровки селекторов
        info = await page.evaluate(
            """() => {
                const q = sel => Array.from(document.querySelectorAll(sel)).slice(0, 8)
                    .map(el => ({
                        tag: el.tagName,
                        id: el.id,
                        cls: el.className && el.className.toString().slice(0, 120),
                        role: el.getAttribute('role'),
                        ariaLabel: el.getAttribute('aria-label'),
                        placeholder: el.getAttribute('placeholder'),
                        text: (el.innerText || '').slice(0, 60),
                    }));
                return {
                    textareas: q('textarea'),
                    contenteditables: q('[contenteditable=\\'true\\']'),
                    buttons: q('button'),
                    fileInputs: q("input[type='file']"),
                    imgs: q('img'),
                    videos: q('video'),
                };
            }"""
        )
        for name, items in info.items():
            logger.info("--- {} ({}) ---", name, len(items))
            for i, it in enumerate(items):
                logger.info("  [{}] {}", i, it)


def _cli() -> None:
    if len(sys.argv) < 3:
        print("usage: python -m app.bots.outsee recon-image|recon-video <prompt> [start_frame]")
        sys.exit(1)
    cmd, prompt = sys.argv[1], sys.argv[2]
    start = sys.argv[3] if len(sys.argv) > 3 else None
    kind = "image" if "image" in cmd else "video"
    asyncio.run(_recon(kind, prompt, start))


if __name__ == "__main__":
    _cli()
