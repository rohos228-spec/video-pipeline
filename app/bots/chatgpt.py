"""ChatGPT web (chat.openai.com / chatgpt.com) через Playwright CDP.

Сценарий использования:
    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        await gpt.new_conversation()
        text = await gpt.ask(prompt)

Селекторы собраны с запасом — ChatGPT регулярно меняет DOM, поэтому здесь
несколько резервных селекторов. При больших ломках — обновить константы.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from playwright.async_api import Page

from app.bots.browser import BrowserSession

CHATGPT_URL = "https://chatgpt.com/"

# Селекторы (несколько вариантов — берём первый, который нашёлся).
INPUT_SELECTORS = [
    "div#prompt-textarea[contenteditable='true']",
    "textarea#prompt-textarea",
    "textarea[data-id='root']",
    "div[contenteditable='true'][data-id='root']",
]
SEND_BUTTON_SELECTORS = [
    "button[data-testid='send-button']",
    "button[aria-label='Send prompt']",
    "button[aria-label='Отправить сообщение']",
    "button[aria-label*='Send']",
]
STOP_BUTTON_SELECTORS = [
    "button[data-testid='stop-button']",
    "button[aria-label='Stop generating']",
    "button[aria-label='Остановить генерацию']",
]
LAST_MESSAGE_SELECTOR = (
    "[data-message-author-role='assistant']:last-of-type "
    "div.markdown, "
    "[data-message-author-role='assistant']:last-of-type"
)
NEW_CHAT_SELECTORS = [
    "a[href='/']",
    "button[aria-label='New chat']",
    "button[aria-label='Новый чат']",
]


async def _first_matching(page: Page, selectors: list[str], *, timeout: float = 10) -> str | None:
    """Находит первый селектор, по которому есть элемент."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for sel in selectors:
            try:
                if await page.locator(sel).count() > 0:
                    return sel
            except Exception:  # noqa: BLE001
                continue
        await asyncio.sleep(0.25)
    return None


class ChatGPTBot:
    def __init__(self, session: BrowserSession) -> None:
        self.session = session
        self._page: Page | None = None

    async def _page_ready(self) -> Page:
        if self._page is None or self._page.is_closed():
            self._page = await self.session.open_page(CHATGPT_URL, reuse=True)
            # ждём, пока загрузится UI
            await _first_matching(self._page, INPUT_SELECTORS, timeout=30)
        return self._page

    async def new_conversation(self) -> None:
        page = await self._page_ready()
        # Самый надёжный способ открыть новый чат — просто перейти на /.
        # Клик по кнопке в сайдбаре ChatGPT часто перехватывается svg-иконкой
        # (`subtree intercepts pointer events`), а навигация работает всегда.
        try:
            await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:  # noqa: BLE001
            # Если страница закрылась или ещё что — пересоздадим вкладку.
            self._page = None
            page = await self._page_ready()
        await _first_matching(page, INPUT_SELECTORS, timeout=30)

    async def _send_prompt(self, text: str) -> None:
        page = await self._page_ready()
        input_sel = await _first_matching(page, INPUT_SELECTORS, timeout=30)
        if not input_sel:
            raise RuntimeError("ChatGPT: не найден input для промта")

        locator = page.locator(input_sel).first
        await locator.click()
        # Вставляем текст через clipboard-free способ — keyboard.type надёжен
        # для contenteditable. Для очень больших текстов используем JS set.
        if len(text) > 8000:
            # Большие промты — через JS, иначе type слишком долгий.
            await page.evaluate(
                """([sel, t]) => {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    if (el.tagName === 'TEXTAREA') {
                        el.focus();
                        el.value = t;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                    } else {
                        el.focus();
                        el.innerText = t;
                        el.dispatchEvent(new InputEvent('input', {bubbles: true, data: t}));
                    }
                }""",
                [input_sel, text],
            )
        else:
            await locator.type(text, delay=3)

        # Находим кнопку отправки — ждём, пока она активна
        send_sel = await _first_matching(page, SEND_BUTTON_SELECTORS, timeout=15)
        if send_sel:
            try:
                await page.locator(send_sel).first.click()
                return
            except Exception:  # noqa: BLE001
                pass
        # запасной путь — Enter (в contenteditable ChatGPT реагирует)
        await page.keyboard.press("Enter")

    async def _wait_until_done(self, *, timeout: float = 300) -> None:
        """Ждём, пока пропадёт кнопка "Stop generating"."""
        page = await self._page_ready()
        deadline = asyncio.get_event_loop().time() + timeout
        # Сначала даём UI подгрузить кнопку stop (появится почти сразу)
        await asyncio.sleep(0.8)
        while asyncio.get_event_loop().time() < deadline:
            still_generating = False
            for sel in STOP_BUTTON_SELECTORS:
                try:
                    if await page.locator(sel).count() > 0:
                        still_generating = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not still_generating:
                # ещё 1.5 сек на docontextualise
                await asyncio.sleep(1.5)
                return
            await asyncio.sleep(0.5)
        raise TimeoutError("ChatGPT: таймаут ожидания ответа")

    async def _read_last_reply(self) -> str:
        page = await self._page_ready()
        # Берём последний assistant-message целиком
        text = await page.evaluate(
            """() => {
                const msgs = document.querySelectorAll("[data-message-author-role='assistant']");
                if (msgs.length === 0) return '';
                const last = msgs[msgs.length - 1];
                return last.innerText || '';
            }"""
        )
        return (text or "").strip()

    async def ask(self, prompt: str, *, timeout: float = 300) -> str:
        """Отправить один промт в текущий чат и вернуть финальный ответ."""
        await self._send_prompt(prompt)
        await self._wait_until_done(timeout=timeout)
        reply = await self._read_last_reply()
        logger.info("ChatGPT reply len={}", len(reply))
        return reply

    async def ask_fresh(self, prompt: str, *, timeout: float = 300) -> str:
        """Новый чат + один промт + ответ."""
        await self.new_conversation()
        return await self.ask(prompt, timeout=timeout)
