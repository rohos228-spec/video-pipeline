"""11Labs (elevenlabs.io) — генерация озвучки через web-интерфейс
Text-to-Speech. Шаг 10 пайплайна.

Архитектура:
  - Браузер: обычный Chrome через CDP (см. `app/bots/browser.py`). В этом
    Chrome'е пользователь уже залогинен на elevenlabs.io (и на остальных AI-
    сервисах — chatgpt, outsee и т.д.). Используем тот же `BrowserSession`,
    что и другие шаги пайплайна.
  - Голос: имя из dropdown'а столбца U topics.xlsx (`prompts/voices.json`).
    Бот открывает TTS-страницу, кликает в правой панели Settings → Voice,
    выбирает голос с нужным именем.
  - Модель: всегда **Eleven v3** (требование пользователя). Выставляется
    в правой панели Settings → Model.
  - Текст: «сырой» закадровый текст из `data/videos/<slug>/voiceover.txt`,
    собирается шагом 2 (make_script).

Логика TTS:
  1) Открыть `https://elevenlabs.io/app/speech-synthesis` (редиректит на
     актуальный URL TTS — `/app/talk-to/text-to-speech` или `/app/text-to-speech`).
  2) В правой панели Settings → Voice — открыть picker и выбрать голос по
     `voice_name`.
  3) В правой панели Settings → Model — открыть picker и выбрать «Eleven v3».
  4) Найти textarea для текста, очистить старое и вставить весь script.
  5) Нажать кнопку Generate speech.
  6) Дождаться появления Download (иконка стрелки рядом с Enhance) и скачать mp3.

⚠️ Селекторы 11labs регулярно меняются. Сохраняем набор кандидатов и берём
первый видимый. Для отладки на машине пользователя:
    python -m app.bots.elevenlabs recon
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Protocol

from loguru import logger
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PWTimeoutError

from app.bots.browser import browser_session
from app.settings import settings

# Поле для текста озвучки. На новом TTS-UI — div[contenteditable] (RTE),
# на старом — textarea. Кладём и то и другое; берём первый видимый.
INPUT_SELECTORS = [
    "div[contenteditable='true'][role='textbox']",
    "div[contenteditable='true']",
    "textarea[placeholder*='text' i]",
    "textarea[placeholder*='введите' i]",
    "textarea[placeholder*='your text' i]",
    "textarea[name='text']",
    "textarea",
]

# Кнопка «Generate speech» (новый UI) / «Generate» (старый).
GENERATE_SELECTORS = [
    "button:has-text('Generate speech')",
    "button:has-text('Generate Speech')",
    "button:has-text('Generate')",
    "button:has-text('Сгенерировать')",
    "button[data-testid='generate']",
    "button[type='submit']",
]

# Кнопка / иконка скачивания готового mp3. После Generate на месте «Enhance»
# рядом появляется кнопка-стрелка с aria-label «Download» или <a download>.
DOWNLOAD_SELECTORS = [
    "a[download][href*='.mp3']",
    "a[download]",
    "button[aria-label='Download']",
    "button[aria-label*='Download' i]",
    "button:has-text('Download')",
    "button:has-text('Скачать')",
]

# Открыватели правой панели Settings → Voice. У 11labs кнопка с текущим
# голосом — это обычно «button:has-text('<voice name>')» или элемент
# с aria-label вроде «Voice picker».
VOICE_OPENER_SELECTORS = [
    "[data-testid='voice-selector']",
    "button[aria-label='Voice']",
    "button[aria-label*='voice' i]",
    # На скринах юзера справа есть карточка-кнопка с именем голоса —
    # её селектор у 11labs нестабильный, поэтому открываем через текст
    # «Voice» в правой панели как fallback (см. ниже в коде).
]

# Открыватели Model. Аналогично — стабильного data-testid нет.
MODEL_OPENER_SELECTORS = [
    "[data-testid='model-selector']",
    "button[aria-label='Model']",
    "button[aria-label*='model' i]",
]

# Текст, который точно стоит на кнопке/строке нужной модели.
MODEL_V3_LABEL = "Eleven v3"


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


async def _click_in_picker_by_text(page: Page, text: str, *, timeout_ms: int = 15_000) -> bool:
    """Внутри открытой выпадашки (модалка / popover) найти элемент с точным
    или содержащим текстом и кликнуть. Возвращает True если кликнули."""
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    candidates = [
        # role-based — самый стабильный путь
        ("role=option", text),
        ("role=menuitem", text),
        # текстовый матч в любом элементе
        (f"text={text!r}", None),
        (f"text=/{text}/i", None),
    ]
    while asyncio.get_event_loop().time() < deadline:
        for sel, opt_text in candidates:
            try:
                if opt_text is not None:
                    loc = page.get_by_role(
                        sel.split("=", 1)[1], name=opt_text, exact=False  # type: ignore[arg-type]
                    )
                else:
                    loc = page.locator(sel)
                if await loc.count() == 0:
                    continue
                first = loc.first
                if await first.is_visible():
                    await first.click()
                    return True
            except Exception:  # noqa: BLE001
                continue
        await asyncio.sleep(0.3)
    return False


async def _select_voice(page: Page, voice_name: str) -> None:
    """Выбрать голос по имени в правой панели Settings → Voice.

    Стратегия: ищем кнопку-карточку, на которой написан текущий voice name
    (любой), кликаем в неё → открывается picker → ищем строку с нужным
    именем и кликаем. Если не получилось — пробуем кандидатные селекторы
    `VOICE_OPENER_SELECTORS`.
    """
    # Сначала пробуем явные селекторы.
    opener = await _first_visible(page, VOICE_OPENER_SELECTORS, timeout_ms=3_000)
    clicked = False
    if opener:
        try:
            await page.locator(opener).first.click()
            clicked = True
        except Exception as e:  # noqa: BLE001
            logger.warning("11labs: voice opener {} клик упал: {}", opener, e)

    # Fallback: ищем в правой панели заголовок «Voice» и кликаем по ближайшей
    # кнопке/карточке под ним.
    if not clicked:
        try:
            label = page.get_by_text("Voice", exact=True).first
            await label.wait_for(timeout=5_000)
            # ближайшая кликабельная карточка — следующий sibling
            await label.locator(
                "xpath=following::*[self::button or @role='button' or self::a][1]"
            ).first.click()
            clicked = True
        except Exception as e:  # noqa: BLE001
            logger.warning("11labs: voice fallback opener упал: {}", e)

    if not clicked:
        raise RuntimeError("11Labs: не смог открыть voice picker")

    # В открытом picker'е выбираем нужный голос.
    ok = await _click_in_picker_by_text(page, voice_name, timeout_ms=15_000)
    if not ok:
        raise RuntimeError(f"11Labs: голос '{voice_name}' не найден в picker'е")
    # Дать UI обновить выбранную карточку.
    await asyncio.sleep(0.8)


async def _select_model_v3(page: Page) -> None:
    """Выбрать модель «Eleven v3» в правой панели Settings → Model.

    Если она уже выбрана (видна на кнопке) — ничего не делаем.
    """
    # Если рядом с «Model» уже есть текст «Eleven v3» — пропускаем.
    try:
        cnt = await page.locator(f"text={MODEL_V3_LABEL!r}").count()
        if cnt > 0:
            # Уже выбрана? Дополнительная проверка — голос с надписью v3 в
            # кнопке Settings панели.
            return
    except Exception:  # noqa: BLE001
        pass

    opener = await _first_visible(page, MODEL_OPENER_SELECTORS, timeout_ms=3_000)
    clicked = False
    if opener:
        try:
            await page.locator(opener).first.click()
            clicked = True
        except Exception as e:  # noqa: BLE001
            logger.warning("11labs: model opener {} клик упал: {}", opener, e)

    if not clicked:
        try:
            label = page.get_by_text("Model", exact=True).first
            await label.wait_for(timeout=5_000)
            await label.locator(
                "xpath=following::*[self::button or @role='button' or self::a][1]"
            ).first.click()
            clicked = True
        except Exception as e:  # noqa: BLE001
            logger.warning("11labs: model fallback opener упал: {}", e)

    if not clicked:
        # Не критично — может быть уже Eleven v3.
        logger.warning("11labs: не смог открыть model picker, продолжаю как есть")
        return

    ok = await _click_in_picker_by_text(page, MODEL_V3_LABEL, timeout_ms=10_000)
    if not ok:
        logger.warning("11labs: '{}' в model picker не нашёл, продолжаю", MODEL_V3_LABEL)
    await asyncio.sleep(0.5)


class ElevenLabsBot:
    """Озвучка через 11labs.

    Использование (см. `app/orchestrator/steps/generate_audio.py`):
        async with browser_session() as bs:
            el = ElevenLabsBot(bs)
            await el.tts(
                text=voiceover_text,
                out_path=audio_path,
                voice_name="Liam - Energetic, Social Media Creator",
            )
    """

    def __init__(self, session: _PageProvider) -> None:
        self.session = session

    async def tts(
        self,
        text: str,
        out_path: Path,
        *,
        voice_name: str | None = None,
        voice_url: str | None = None,
        timeout: float = 600,
    ) -> Path:
        if not text or not text.strip():
            raise RuntimeError("11Labs: пустой текст для озвучки")
        target_url = voice_url or settings.elevenlabs_web_url
        logger.info("11labs: открываю {}", target_url)
        page = await self.session.open_page(target_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        # На свежеоткрытой странице UI грузится не сразу — даём 11labs время.
        await asyncio.sleep(2.0)

        # 1) Голос
        if voice_name:
            logger.info("11labs: выбираю голос '{}'", voice_name)
            await _select_voice(page, voice_name)

        # 2) Модель — всегда Eleven v3
        await _select_model_v3(page)

        # 3) Текст
        input_sel = await _first_visible(page, INPUT_SELECTORS, timeout_ms=60_000)
        if not input_sel:
            raise RuntimeError("11Labs: не найдено поле для текста")
        loc = page.locator(input_sel).first
        await loc.click()
        try:
            # Для textarea — fill сразу заменяет. Для contenteditable —
            # нужно сначала очистить, потом вставить через keyboard.type/insert.
            tag = (await loc.evaluate("el => el.tagName") or "").lower()
        except Exception:  # noqa: BLE001
            tag = ""
        if tag == "textarea":
            await loc.fill(text)
        else:
            # contenteditable: select all + delete + insertText
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            try:
                await loc.evaluate(
                    "(el, t) => { el.focus(); document.execCommand('insertText', false, t); }",
                    text,
                )
            except Exception:  # noqa: BLE001
                # Fallback на keyboard.type (медленнее, но работает).
                await page.keyboard.type(text)

        # 4) Generate
        gen_sel = await _first_visible(page, GENERATE_SELECTORS, timeout_ms=15_000)
        if not gen_sel:
            raise RuntimeError("11Labs: не найдена кнопка Generate speech")
        await page.locator(gen_sel).first.click()
        logger.info("11labs: Generate speech нажат, ждём mp3")

        # 5) Скачать mp3
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


async def _recon() -> None:
    """Полу-ручная разведка селекторов прямо в Chrome (CDP).

    Запуск: `python -m app.bots.elevenlabs recon`. Открывает TTS-страницу
    через тот же `BrowserSession`, печатает что нашёл из ключевых элементов.
    """
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
                        role: el.getAttribute('role'),
                        text: (el.innerText || '').slice(0, 80),
                    }));
                return {
                    contenteditables: q("div[contenteditable='true']"),
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
    if len(sys.argv) < 2 or sys.argv[1] != "recon":
        print("usage: python -m app.bots.elevenlabs recon")
        sys.exit(1)
    asyncio.run(_recon())


if __name__ == "__main__":
    _cli()
