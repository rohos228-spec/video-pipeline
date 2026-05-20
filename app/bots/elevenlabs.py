"""11Labs (elevenlabs.io) — генерация озвучки через web-интерфейс
Speech Synthesis. Шаг 10 пайплайна.

Архитектура:
  - Браузер: Dolphin Anty (см. `app/bots/dolphin.py`). 11Labs агрессивно лочит
    обычные браузерные сессии без правильного fingerprint'а, поэтому шаг 10
    ходит через антидетект-профиль, а не через общий Chrome-CDP.
  - Голос: имя из dropdown'а столбца U topics.xlsx (`prompts/voices.json`).
    Каждой записи `name` соответствует `url` — прямая ссылка на конкретный
    голос на сайте 11labs. Бот открывает этот URL → 11labs сам подставит
    в Text-to-Speech именно нужный голос.
  - Текст: «сырой» закадровый текст из `data/videos/<slug>/voiceover.txt`,
    собирается шагом 2 (make_script).

Логика TTS:
  1) Открыть voice_url (страница голоса с кнопкой "Use voice"/Text-to-Speech).
  2) Найти textarea (или contenteditable) для текста, вставить весь script.
  3) Нажать кнопку Generate.
  4) Дождаться появления Download и скачать mp3.

⚠️ Селекторы 11labs регулярно меняются. Сохраняем набор кандидатов и берём
первый видимый. Для отладки на машине пользователя: `python -m app.bots.elevenlabs recon "..."`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Protocol

from loguru import logger
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PWTimeoutError

from app.bots.dolphin import dolphin_session
from app.settings import settings

INPUT_SELECTORS = [
    # 11labs официальный Speech Synthesis: textarea с placeholder.
    "textarea[placeholder*='text' i]",
    "textarea[placeholder*='введите' i]",
    "textarea[placeholder*='your text' i]",
    "textarea[name='text']",
    "div[contenteditable='true']",
    "textarea",
]
GENERATE_SELECTORS = [
    "button:has-text('Generate speech')",
    "button:has-text('Generate')",
    "button:has-text('Сгенерировать')",
    "button:has-text('Create')",
    "button[type='submit']",
    "button[data-testid='generate']",
]
DOWNLOAD_SELECTORS = [
    "a[download][href*='.mp3']",
    "button:has-text('Download')",
    "button:has-text('Скачать')",
    "button[aria-label='Download']",
    "a[aria-label='Download']",
]
# Дополнительно: для voice-страницы кнопка "Use voice" → переход в Speech Synthesis.
USE_VOICE_SELECTORS = [
    "button:has-text('Use voice')",
    "button:has-text('Use this voice')",
    "button:has-text('Использовать голос')",
    "a:has-text('Use voice')",
]


class _PageProvider(Protocol):
    """Минимальный интерфейс браузерной сессии — `open_page(url, reuse)`.

    И `app.bots.browser.BrowserSession`, и `app.bots.dolphin.DolphinSession`
    его реализуют, поэтому `ElevenLabsBot` принимает любую."""

    async def open_page(self, url: str, *, reuse: bool = True) -> Page: ...


async def _first_visible(
    page: Page, selectors: list[str], *, timeout_ms: int = 20_000
) -> str | None:
    """Вернуть первый видимый селектор из списка, ожидая до `timeout_ms`."""
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
    """Озвучка через 11labs.

    Использование (см. `app/orchestrator/steps/generate_audio.py`):
        async with dolphin_session() as bs:
            el = ElevenLabsBot(bs)
            await el.tts(
                text=voiceover_text,
                out_path=audio_path,
                voice_url="https://elevenlabs.io/...<voice>",
            )

    Если `voice_url` пустой — открываем дефолтную страницу Speech Synthesis
    (`settings.elevenlabs_web_url`), и голос выбирает пользователь заранее
    в самом 11labs UI этого профиля.
    """

    def __init__(self, session: _PageProvider) -> None:
        self.session = session

    async def tts(
        self,
        text: str,
        out_path: Path,
        *,
        voice_url: str | None = None,
        timeout: float = 600,
    ) -> Path:
        if not text or not text.strip():
            raise RuntimeError("11Labs: пустой текст для озвучки")
        target_url = voice_url or settings.elevenlabs_web_url
        logger.info("11labs: открываю {}", target_url)
        page = await self.session.open_page(target_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")

        # Если страница голоса — иногда нужно нажать "Use voice" чтобы попасть
        # в Speech Synthesis с уже выбранным голосом.
        use_sel = await _first_visible(page, USE_VOICE_SELECTORS, timeout_ms=3_000)
        if use_sel:
            try:
                await page.locator(use_sel).first.click()
                await page.wait_for_load_state("domcontentloaded")
            except Exception as e:  # noqa: BLE001
                logger.warning("11labs: не смог нажать 'Use voice': {}", e)

        # Поле для текста
        input_sel = await _first_visible(page, INPUT_SELECTORS, timeout_ms=60_000)
        if not input_sel:
            raise RuntimeError("11Labs: не найден textarea для текста")
        await page.locator(input_sel).first.click()
        # fill быстрее и работает на textarea / contenteditable.
        await page.locator(input_sel).first.fill(text)

        # Кнопка Generate
        gen_sel = await _first_visible(page, GENERATE_SELECTORS, timeout_ms=15_000)
        if not gen_sel:
            raise RuntimeError("11Labs: не найдена кнопка Generate")
        await page.locator(gen_sel).first.click()

        # Ждём появления Download и скачиваем mp3
        out_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = asyncio.get_event_loop().time() + timeout
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
                # Альтернативный путь: <a download href="...">
                href = await page.locator(sel).first.get_attribute("href")
                if href:
                    ctx = page.context
                    resp = await ctx.request.get(href)
                    if resp.status < 400:
                        out_path.write_bytes(await resp.body())
                        logger.info("11Labs mp3 saved (via href) → {}", out_path)
                        return out_path
                await asyncio.sleep(1.0)
                continue
        raise PWTimeoutError(f"11Labs: не дождались появления Download за {timeout}s")


# ---- CLI для разведки селекторов ------------------------------------------


async def _recon(text: str) -> None:
    """Полу-ручная разведка селекторов на твоей машине.

    Запуск: `python -m app.bots.elevenlabs recon "тестовый текст"`. Используем
    тот же Dolphin Anty профиль, что и боевой шаг 10."""
    async with dolphin_session() as bs:
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
