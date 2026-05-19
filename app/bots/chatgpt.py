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
import base64
import mimetypes
from pathlib import Path

from loguru import logger
from playwright.async_api import Download, Page

from app.bots.browser import BrowserSession

CHATGPT_URL = "https://chatgpt.com/"

# Селекторы для file-input и download-ссылок в чате (с запасом).
FILE_INPUT_SELECTORS = [
    "input[type='file'][multiple]",
    "input[type='file']",
]
# Кнопка-скрепка (paperclip) — нужна, чтобы триггернуть появление
# input[type=file], который ChatGPT иногда создаёт лениво только после клика.
# В новых билдах клик по скрепке открывает поповер-меню («Add photos and
# files» / «Загрузить файлы и изображения» / «Connect to Google Drive» …),
# и input[type=file] появляется только после клика по самому пункту меню.
ATTACH_BUTTON_SELECTORS = [
    "button[aria-label='Attach files']",
    "button[aria-label*='Attach']",
    "button[aria-label='Прикрепить файлы']",
    "button[aria-label*='Прикрепить']",
    "button[data-testid='composer-attach-files-button']",
    "button[data-testid='composer-attach-button']",
    "button[data-testid='composer-plus-btn']",
    "button[aria-label='Add photos and files']",
    "button[aria-label*='Add photos']",
    "button[aria-haspopup='menu'][aria-label*='Attach']",
    # Совсем новый UI — кнопка "+" слева от поля ввода.
    "form button[aria-label='Add']",
    "form [data-testid='composer-action-file-upload']",
]
# Пункты меню, которые могут появиться после клика по скрепке/«+».
# Кликаем первый, который виден — это триггерит выбор файла (или открывает
# системный picker; нам он не нужен — мы используем set_input_files).
ATTACH_MENU_ITEM_SELECTORS = [
    "[role='menuitem']:has-text('Add photos and files')",
    "[role='menuitem']:has-text('Загрузить файлы и изображения')",
    "[role='menuitem']:has-text('Загрузить файлы')",
    "[role='menuitem']:has-text('Upload from computer')",
    "[role='menuitem']:has-text('Загрузить с компьютера')",
    "[role='menuitem']:has-text('From computer')",
    "[role='menuitem']:has-text('С компьютера')",
    # Иногда это просто <div> или <button> с подобным текстом.
    "div:has-text('Add photos and files'):has(svg)",
    "button:has-text('Upload from computer')",
]
# Превью прикреплённого файла в композере — индикатор успешной загрузки.
# В разных билдах превью может быть как в `data-testid`, так и в кнопке
# «Remove file», или просто в форме как карточка с именем файла.
FILE_PREVIEW_SELECTORS = [
    "div[data-testid*='file-preview']",
    "div[data-testid*='attachment']",
    "[data-testid='composer-file-attachment']",
    "[data-testid*='attached-file']",
    "div.group\\/attachment",
    "div[role='button'][aria-label*='Remove']",
    "button[aria-label*='Remove file']",
    "button[aria-label*='Удалить файл']",
    # Карточка с именем загруженного файла в форме композера.
    "form [class*='attachment']",
    "form [class*='preview']",
]
# Селекторы для скачивания сгенерированного файла в ответе ассистента.
# ChatGPT часто рендерит файл как карточку с кнопкой скачивания, у которой
# aria-label="Download" / "Скачать" / data-testid="..."  Иногда это <a download>,
# иногда <button>, в новых билдах — обёртка с svg-иконкой и event-handler-ом
# на самой кнопке. Селекторы перебираются по порядку.
ASSISTANT_LAST_PREFIX = "[data-message-author-role='assistant']:last-of-type"
# Хэш sprite-иконки скачивания внутри svg-use в карточке файла.
# Текущий хэш на 2025-Q4: '#1a3695' (рядом с ним '#03424d' — share/options).
# При обновлении ChatGPT хэши могут поменяться — тогда дампим outerHTML
# и подставляем новые сюда.
DOWNLOAD_SPRITE_HASHES = ["1a3695"]
DOWNLOAD_LINK_SELECTORS = [
    f"{ASSISTANT_LAST_PREFIX} a[download]",
    f"{ASSISTANT_LAST_PREFIX} a[href*='/files/']",
    f"{ASSISTANT_LAST_PREFIX} a[href*='sandbox']",
    f"{ASSISTANT_LAST_PREFIX} a[href*='.xlsx']",
    f"{ASSISTANT_LAST_PREFIX} a[href*='.txt']",
    f"{ASSISTANT_LAST_PREFIX} button[aria-label='Download']",
    f"{ASSISTANT_LAST_PREFIX} button[aria-label='Скачать']",
    f"{ASSISTANT_LAST_PREFIX} button[aria-label*='Download']",
    f"{ASSISTANT_LAST_PREFIX} button[aria-label*='Скачать']",
    f"{ASSISTANT_LAST_PREFIX} button[data-testid*='download']",
    f"{ASSISTANT_LAST_PREFIX} a[aria-label='Download']",
    f"{ASSISTANT_LAST_PREFIX} a[aria-label='Скачать']",
    # Карточка файла в новом UI: <button> с svg <use href=".../sprites...#<hash>">.
    # `:has()` поддерживается Playwright/Chromium >=105.
    *[
        f"{ASSISTANT_LAST_PREFIX} button:has(use[href$='#{h}'])"
        for h in DOWNLOAD_SPRITE_HASHES
    ],
    # Fallback: любая кнопка/ссылка внутри карточки файла.
    f"{ASSISTANT_LAST_PREFIX} [data-testid*='file'] button",
    f"{ASSISTANT_LAST_PREFIX} [data-testid*='attachment'] button",
    # Popover скачивания (Radix) — появляется после клика по карточке файла.
    "[data-radix-popper-content-wrapper] a[download]",
    "[data-radix-popper-content-wrapper] a[href*='/files/']",
    "[data-radix-popper-content-wrapper] a[href*='sandbox']",
    "[data-radix-popper-content-wrapper] button[aria-label*='Download']",
    "[data-radix-popper-content-wrapper] button[aria-label*='Скачать']",
    "[data-radix-popper-content-wrapper] a",
]
# Карточка файла как таковая — иногда нужно сначала открыть её
# (двойной клик / hover), чтобы появилась кнопка Download.
FILE_CARD_SELECTORS = [
    f"{ASSISTANT_LAST_PREFIX} [data-testid*='file']",
    f"{ASSISTANT_LAST_PREFIX} [data-testid*='attachment']",
    f"{ASSISTANT_LAST_PREFIX} div[role='button']:has(svg)",
    # Новый UI 2025-Q2: файл = <button class="behavior-btn"> внутри
    # <span data-state="closed"> (Radix trigger). Клик открывает popover.
    f"{ASSISTANT_LAST_PREFIX} span[data-state] > button.behavior-btn",
    f"{ASSISTANT_LAST_PREFIX} button.behavior-btn",
]

# Селекторы (несколько вариантов — берём первый, который нашёлся).
INPUT_SELECTORS = [
    "div#prompt-textarea[contenteditable='true']",
    "textarea#prompt-textarea",
    "textarea[data-id='root']",
    "div[contenteditable='true'][data-id='root']",
]
SEND_BUTTON_SELECTORS = [
    # Свежий UI 2025-Q2/Q3 — composer-submit-button
    "button[data-testid='composer-submit-button']",
    "button[data-testid='fruitjuice-send-button']",
    "button#composer-submit-button",
    # Старый testid (если ещё не удалили)
    "button[data-testid='send-button']",
    # aria-label варианты — англ./рус., в т.ч. "Send message", "Отправить промт"
    "button[aria-label='Send message']",
    "button[aria-label='Send prompt']",
    "button[aria-label='Send']",
    "button[aria-label='Отправить сообщение']",
    "button[aria-label='Отправить промт']",
    "button[aria-label='Отправить']",
    "button[aria-label*='Send']",
    "button[aria-label*='Отправить']",
    # Композер-форма + submit-кнопка (универсальный fallback)
    "form[data-type='unified-composer'] button[type='submit']",
    "main form button[type='submit']",
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

# Модалка «войдите/зарегистрируйтесь», которую ChatGPT показывает в анонимном
# режиме и которая перекрывает клики по странице. Дизмиссим её, если появилась.
NO_AUTH_MODAL_SELECTOR = "[data-testid='modal-no-auth-login']"
NO_AUTH_DISMISS_SELECTORS = [
    "[data-testid='modal-no-auth-login'] button:has-text('Stay logged out')",
    "[data-testid='modal-no-auth-login'] button:has-text('Остаться без входа')",
    "[data-testid='modal-no-auth-login'] a:has-text('Stay logged out')",
    "[data-testid='modal-no-auth-login'] a:has-text('Остаться без входа')",
    "[data-testid='modal-no-auth-login'] button[aria-label='Close']",
    "[data-testid='modal-no-auth-login'] button:has-text('Maybe later')",
    "[data-testid='modal-no-auth-login'] button:has-text('Позже')",
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
            await self._dismiss_no_auth_modal(self._page)
        return self._page

    async def _dismiss_no_auth_modal(self, page: Page) -> None:
        """Если на странице висит модалка «войдите/зарегистрируйтесь» —
        пытаемся её закрыть, чтобы клики по prompt-полю не перехватывались.
        Если не смогли закрыть кнопкой — жмём Escape (часто работает).
        """
        try:
            if await page.locator(NO_AUTH_MODAL_SELECTOR).count() == 0:
                return
        except Exception:  # noqa: BLE001
            return
        logger.info("ChatGPT: обнаружена модалка no-auth, пытаюсь закрыть")
        for sel in NO_AUTH_DISMISS_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click(timeout=3_000)
                    await asyncio.sleep(0.5)
                    if await page.locator(NO_AUTH_MODAL_SELECTOR).count() == 0:
                        logger.info("ChatGPT: модалка закрыта селектором {}", sel)
                        return
            except Exception:  # noqa: BLE001
                continue
        # fallback — Escape
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
        except Exception:  # noqa: BLE001
            pass
        if await page.locator(NO_AUTH_MODAL_SELECTOR).count() > 0:
            logger.warning(
                "ChatGPT: модалка no-auth не закрывается автоматически. "
                "Если не залогинен — залогинься в том же Chrome (или закрой её руками)."
            )

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
        await self._dismiss_no_auth_modal(page)

    async def _send_prompt(self, text: str) -> None:
        page = await self._page_ready()
        await self._dismiss_no_auth_modal(page)
        input_sel = await _first_matching(page, INPUT_SELECTORS, timeout=30)
        if not input_sel:
            raise RuntimeError("ChatGPT: не найден input для промта")

        locator = page.locator(input_sel).first
        await locator.click()
        # Убеждаемся, что поле сфокусировано и пустое. ProseMirror игнорирует
        # прямое присвоение innerText — поэтому используем CDP Input.insertText
        # через page.keyboard.insertText: он посылает один beforeinput/input
        # событие с полным текстом, и ProseMirror корректно обновляет состояние.
        await locator.focus()
        # Очищаем возможный предыдущий ввод (Ctrl+A → Delete).
        try:
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Delete")
        except Exception:  # noqa: BLE001
            pass
        await page.keyboard.insert_text(text)
        # Небольшая пауза, чтобы кнопка Send активировалась.
        await asyncio.sleep(0.5)
        logger.info("ChatGPT: текст промта введён ({} символов), ищу Send", len(text))

        # Находим кнопку отправки. ВАЖНО: после аплоада тяжёлых файлов (xlsx,
        # несколько картинок) кнопка может оставаться `disabled` несколько
        # секунд — пока ChatGPT доинициализирует upload. Поэтому таймаут
        # тут ДОЛГИЙ (120с), и мы ждём не просто появления селектора в DOM,
        # а пока кнопка станет enabled (без атрибута `disabled` и без
        # `aria-disabled='true'`). Это полностью убирает ситуацию, когда
        # фоллбек на Enter срабатывает раньше, чем активируется реальная
        # Send-кнопка.
        send_sel = await self._wait_for_enabled_send_button(page, timeout=120.0)
        if send_sel:
            logger.info("ChatGPT: Send button найдена и активна ({})", send_sel)
            try:
                await page.locator(send_sel).first.click(timeout=10_000)
                logger.info("ChatGPT: Send button нажата успешно")
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ChatGPT: Send button найдена ({}), но клик упал: {}",
                    send_sel, e,
                )
        else:
            logger.warning(
                "ChatGPT: Send button НЕ стала активной за 120с — "
                "UI ChatGPT мог измениться или upload завис. "
                "Дамплю композер для диагностики.",
            )
            await self._dump_composer_html()
            await self._dump_send_button_candidates(page)

        # Запасной путь — Enter (в contenteditable ChatGPT часто реагирует).
        # ВАЖНО: между нажатием Send и фоллбеком фокус мог уйти. Нужно
        # явно вернуть фокус на композер и подождать, чтобы UI отработал.
        logger.info("ChatGPT: fallback — refocus composer + Enter")
        try:
            await locator.focus()
            await asyncio.sleep(0.4)
            await page.keyboard.press("Enter")
            await asyncio.sleep(1.5)
            # Проверка: если контент композера всё ещё содержит наш текст —
            # Enter не сработал. Делаем ещё одну попытку через page.evaluate
            # с прямой имитацией keydown.
            still_has_text = False
            try:
                composer_text = await locator.inner_text(timeout=3_000)
                # Сравниваем нормализованные первые 60 символов.
                first = " ".join((text or "").split())[:60].lower()
                got = " ".join((composer_text or "").split())[:60].lower()
                still_has_text = bool(first) and (first in got)
            except Exception:  # noqa: BLE001
                still_has_text = False
            if still_has_text:
                logger.warning(
                    "ChatGPT: после Enter композер не очистился, "
                    "пробую отправку через dispatchEvent keydown"
                )
                try:
                    await page.evaluate(
                        """() => {
                            const el = document.querySelector('main form [contenteditable=\"true\"]')
                                || document.querySelector('[contenteditable=\"true\"]');
                            if (!el) return false;
                            el.focus();
                            const ev = new KeyboardEvent('keydown', {
                                key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                                bubbles: true, cancelable: true
                            });
                            el.dispatchEvent(ev);
                            return true;
                        }"""
                    )
                    await asyncio.sleep(1.5)
                except Exception as e2:  # noqa: BLE001
                    logger.warning("ChatGPT: dispatchEvent Enter упал: {}", e2)
                # Финальная проверка: если после ВСЕХ фоллбеков текст всё
                # ещё сидит в композере — send провалился. Никогда нельзя молча
                # возвращаться — это приводит к ситуации, когда вызывающий видит «ответ
                # длины 0 симв.» и может добавить ещё файлы/текст в тот же застрявший
                # композер. Лучше громко упасть и отдать решение retry-логике выше.
                try:
                    composer_text2 = await locator.inner_text(timeout=3_000)
                    first2 = " ".join((text or "").split())[:60].lower()
                    got2 = " ".join((composer_text2 or "").split())[:60].lower()
                    still_after_all = bool(first2) and (first2 in got2)
                except Exception:  # noqa: BLE001
                    still_after_all = False
                if still_after_all:
                    raise RuntimeError(
                        "ChatGPT: send failed after all fallbacks "
                        "(Send-click + Enter + dispatchEvent) — композер "
                        "всё ещё содержит исходный текст"
                    )
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("ChatGPT: Enter тоже упал: {}", e)

    async def _dump_send_button_candidates(self, page: Page) -> None:
        """Если SEND_BUTTON_SELECTORS не сработали — логируем outerHTML всех
        кнопок внутри композера, чтобы вручную найти правильный селектор."""
        try:
            buttons = await page.evaluate(
                """() => {
                    const form = document.querySelector('main form')
                        || document.querySelector('form[data-type="unified-composer"]')
                        || document.querySelector('form');
                    if (!form) return ['no form found in DOM'];
                    const btns = form.querySelectorAll('button');
                    return Array.from(btns).map(b => b.outerHTML.slice(0, 400));
                }"""
            )
            for i, html in enumerate(buttons or []):
                logger.info("ChatGPT: composer button[{}]: {}", i, html)
            if not buttons:
                logger.warning("ChatGPT: ни одной <button> внутри композера")
        except Exception as e:  # noqa: BLE001
            logger.warning("ChatGPT: dump_send_button_candidates упал: {}", e)

    async def _wait_for_enabled_send_button(
        self, page: Page, *, timeout: float = 120.0,
    ) -> str | None:
        """Ждёт пока в DOM появится Send-кнопка И станет ENABLED.

        Возвращает успешный селектор или None если не дождались. ChatGPT
        отрисовывает Send-кнопку быстро, но держит её `disabled` пока
        идёт upload файлов (xlsx может тяжело инициализироваться). При
        старых таймаутах (8 сек) код проваливался в Enter-фоллбек, который
        работает нестабильно. Этот метод — главный фикс: ждём именно
        «активную» кнопку, а не просто «видна в DOM».

        Кнопку считаем активной если:
        - её селектор найден,
        - на ней НЕТ атрибута `disabled`,
        - НЕТ `aria-disabled='true'`.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        last_log_at = 0.0
        while asyncio.get_event_loop().time() < deadline:
            for sel in SEND_BUTTON_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() == 0:
                        continue
                    is_disabled = await loc.evaluate(
                        """(el) => {
                            if (el.disabled) return true;
                            const ad = el.getAttribute('aria-disabled');
                            if (ad === 'true') return true;
                            return false;
                        }"""
                    )
                    if not is_disabled:
                        return sel
                except Exception:  # noqa: BLE001
                    continue
            # Прогресс-лог раз в 10 сек.
            now = asyncio.get_event_loop().time()
            if now - last_log_at >= 10.0:
                logger.info(
                    "ChatGPT: жду пока Send-кнопка станет enabled "
                    "(прошло ~{:.0f}с / {:.0f}с)",
                    now - (deadline - timeout), timeout,
                )
                last_log_at = now
            await asyncio.sleep(0.5)
        return None

    async def _wait_until_done(self, *, timeout: float = 300) -> None:
        """Ждём, пока пропадёт кнопка "Stop generating".

        Полл каждые 0.25 сек (раньше было 0.5), без длинного initial wait.
        Это значит что для коротких ответов мы перестаём ждать через
        ~0.5 сек после исчезновения кнопки Stop вместо 2.3 сек.
        """
        page = await self._page_ready()
        deadline = asyncio.get_event_loop().time() + timeout
        # Минимальный initial wait чтобы UI успел подгрузить Stop-кнопку.
        # 0.2 сек достаточно — её рендерит React сразу после Send.
        await asyncio.sleep(0.2)
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
                # Короткий tail (0.3 сек) на docontextualise — раньше было 1.5
                await asyncio.sleep(0.3)
                return
            await asyncio.sleep(0.25)
        raise TimeoutError("ChatGPT: таймаут ожидания ответа")

    async def _read_last_reply(self) -> str:
        page = await self._page_ready()
        # Берём последний assistant-message целиком. Перебираем несколько
        # селекторов — ChatGPT периодически меняет атрибуты у контейнеров
        # сообщений (`data-message-author-role` → `data-author-role` и т.п.).
        text = await page.evaluate(
            """() => {
                const sels = [
                    "[data-message-author-role='assistant']",
                    "[data-author-role='assistant']",
                    "[data-message-author='assistant']",
                    "article[data-testid^='conversation-turn-']",
                ];
                for (const sel of sels) {
                    const msgs = document.querySelectorAll(sel);
                    if (msgs.length === 0) continue;
                    // Берём последнее сообщение, которое НЕ от юзера.
                    for (let i = msgs.length - 1; i >= 0; i--) {
                        const m = msgs[i];
                        const role = m.getAttribute('data-message-author-role')
                            || m.getAttribute('data-author-role')
                            || m.getAttribute('data-message-author')
                            || '';
                        if (role && role !== 'assistant') continue;
                        // Если у элемента есть .markdown — берём его, иначе весь innerText.
                        const md = m.querySelector('div.markdown, .markdown-content');
                        const t = (md ? md.innerText : m.innerText) || '';
                        if (t.trim().length > 0) return t;
                    }
                }
                return '';
            }"""
        )
        return (text or "").strip()

    async def ask(self, prompt: str, *, timeout: float = 300) -> str:
        """Отправить один промт в текущий чат и вернуть финальный ответ.

        После того как кнопка «Stop generating» пропала, ждём пока текст
        стабилизируется (не меняется 2 сек подряд), но не дольше 30 сек.
        ChatGPT 5 thinking может дорисовывать ответ — 2 сек стабильности
        достаточно чтобы поймать финальную версию без переожидания.
        """
        await self._send_prompt(prompt)
        await self._wait_until_done(timeout=timeout)

        # Стабилизация текста: не меняется 2 сек подряд (раньше 6), не дольше 30с
        page = await self._page_ready()
        last_text = ""
        stable_for = 0.0
        deadline = asyncio.get_event_loop().time() + 30.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)
            text = await self._read_last_reply()
            # Если кнопка «Stop generating» снова появилась — модель всё ещё
            # генерирует, ждём дальше.
            still_generating = False
            for sel in STOP_BUTTON_SELECTORS:
                try:
                    if await page.locator(sel).count() > 0:
                        still_generating = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if still_generating:
                stable_for = 0.0
                last_text = text
                continue
            if text == last_text and len(text) > 50:
                stable_for += 0.5
                if stable_for >= 2.0:
                    break
            else:
                stable_for = 0.0
                last_text = text

        reply = await self._read_last_reply()
        logger.info("ChatGPT reply len={}", len(reply))
        return reply

    async def ask_fresh(self, prompt: str, *, timeout: float = 300) -> str:
        """Новый чат + один промт + ответ."""
        await self.new_conversation()
        return await self.ask(prompt, timeout=timeout)

    # ---------- File upload / download (для xlsx-пайплайна) -------------------

    async def _attach_files(self, file_paths: list[Path]) -> None:
        """Загружает один или несколько файлов в текущий черновик сообщения.

        Стратегия (на 2026-Q2 ChatGPT):
          1. **Главный путь — drag-and-drop через синтетический DragEvent**
             c DataTransfer, содержащим File-объекты. Это работает как
             ручное перетаскивание файла на форму композера: ChatGPT
             триггерит свой полный upload-pipeline (POST на /backend-api/files)
             и реально грузит файл на сервер. set_input_files в новых билдах
             часто показывает превью, но реальный upload зависает в
             «бесконечной загрузке».
          2. **Fallback — классический set_input_files** через скрытый
             input[type=file]. Используется если drag-drop по какой-то причине
             не сработал. Здесь же сначала кликаем по скрепке/«+» и пункту
             «Add photos and files», чтобы материализовать input в DOM.
          3. **Жёстко ждём превью + завершение upload-spinner**. Если ни один
             способ не привёл к появлению превью в окне ChatGPT — кидаем
             RuntimeError с диагностикой и НЕ отправляем промт.
        """
        if not file_paths:
            raise ValueError("_attach_files: file_paths пустой")

        page = await self._page_ready()
        await self._dismiss_no_auth_modal(page)

        for fp in file_paths:
            if not fp.exists():
                raise FileNotFoundError(f"upload: файл не найден {fp}")

        names = ", ".join(p.name for p in file_paths)
        logger.info("ChatGPT: начинаю аплоад файлов [{}]", names)

        # 1. Главный путь — drag-and-drop.
        drag_drop_ok = False
        try:
            await self._drag_drop_files(file_paths)
            preview_sel = await _first_matching(
                page, FILE_PREVIEW_SELECTORS, timeout=20
            )
            if preview_sel:
                logger.info(
                    "ChatGPT: drag-drop превью появилось ({})", preview_sel
                )
                await self._wait_upload_done(timeout=120)
                drag_drop_ok = True
            else:
                logger.warning(
                    "ChatGPT: drag-drop не дал превью за 20с — "
                    "перехожу на fallback set_input_files"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "ChatGPT: drag-drop упал ({}) — перехожу на fallback "
                "set_input_files",
                e,
            )

        if drag_drop_ok:
            return

        # 2. Fallback — set_input_files через скрытый input[type=file].
        logger.info("ChatGPT: fallback на set_input_files")
        # 2a. Пытаемся найти input[type=file] напрямую.
        input_sel = await _first_matching(page, FILE_INPUT_SELECTORS, timeout=2)
        if input_sel:
            logger.info("ChatGPT: input[type=file] найден сразу ({})", input_sel)

        # 2b. Если не нашли — кликаем по скрепке + поповер-меню.
        if not input_sel:
            attach_sel = await _first_matching(
                page, ATTACH_BUTTON_SELECTORS, timeout=10
            )
            if not attach_sel:
                await self._dump_composer_html()
                raise RuntimeError(
                    "ChatGPT: drag-drop не сработал И не нашёл "
                    "кнопку-скрепку (ATTACH_BUTTON_SELECTORS). "
                    "Возможно изменился UI — пришли скрин окна Chrome "
                    "или строку 'composer outerHTML' из консоли."
                )
            logger.info("ChatGPT: кликаю по скрепке ({})", attach_sel)
            try:
                await page.locator(attach_sel).first.click(timeout=3_000)
                await asyncio.sleep(0.6)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ChatGPT: не смог кликнуть скрепку {}: {}", attach_sel, e
                )

            menu_sel = await _first_matching(
                page, ATTACH_MENU_ITEM_SELECTORS, timeout=2
            )
            if menu_sel:
                logger.info("ChatGPT: кликаю по пункту меню '{}'", menu_sel)
                try:
                    await page.locator(menu_sel).first.click(timeout=3_000)
                    await asyncio.sleep(0.4)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "ChatGPT: не смог кликнуть пункт меню {}: {}",
                        menu_sel,
                        e,
                    )

            input_sel = await _first_matching(
                page, FILE_INPUT_SELECTORS, timeout=10
            )

        if not input_sel:
            await self._dump_composer_html()
            raise RuntimeError(
                "ChatGPT: drag-drop не сработал И не нашёл input[type=file] "
                "даже после клика по скрепке. Возможно изменился UI — "
                "пришли скрин окна Chrome или строки 'composer outerHTML' "
                "из консоли."
            )

        # 2c. set_input_files.
        loc = page.locator(input_sel).last
        await loc.set_input_files([str(p) for p in file_paths])
        logger.info("ChatGPT: set_input_files выполнен через {}", input_sel)

        # 2d. Ждём превью.
        preview_timeout = 60.0
        preview_sel = await _first_matching(
            page, FILE_PREVIEW_SELECTORS, timeout=preview_timeout
        )
        if not preview_sel:
            await self._dump_composer_html()
            raise RuntimeError(
                f"ChatGPT: ни drag-drop, ни set_input_files не сработали — "
                f"за {int(preview_timeout)} сек не появилось превью файла(ов) "
                f"[{names}]. Аплоад НЕ удался — промт в ChatGPT не отправляю."
            )
        logger.info("ChatGPT: превью файла(ов) появилось ({})", preview_sel)

        # 2e. Ждём, пока пропадёт upload-spinner.
        await self._wait_upload_done(timeout=120)

    async def _drag_drop_files(self, file_paths: list[Path]) -> None:
        """Симулирует drag-and-drop файлов на форму композера ChatGPT.

        Читает файлы как base64 в Python, передаёт в JS, конструирует
        File-объекты и DataTransfer, диспатчит dragenter/dragover/drop
        на форму композера. Это вызывает у ChatGPT тот же upload-pipeline,
        что и при ручном перетаскивании файла мышкой.
        """
        page = await self._page_ready()

        files_data: list[dict[str, str]] = []
        for fp in file_paths:
            mime, _ = mimetypes.guess_type(str(fp))
            if not mime:
                ext = fp.suffix.lower()
                if ext == ".xlsx":
                    mime = (
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    )
                elif ext == ".txt":
                    mime = "text/plain"
                elif ext == ".md":
                    mime = "text/markdown"
                elif ext == ".pdf":
                    mime = "application/pdf"
                else:
                    mime = "application/octet-stream"
            with open(fp, "rb") as f:
                content_b64 = base64.b64encode(f.read()).decode("ascii")
            files_data.append(
                {"name": fp.name, "mime": mime, "b64": content_b64}
            )
        logger.info(
            "ChatGPT: drag-drop {} файлов на форму композера",
            len(files_data),
        )

        # Селекторы drop-цели — пробуем от более специфичных к общим.
        # Главное — попасть в элемент, на котором ChatGPT повесил
        # `ondrop`/`ondragover`. Обычно это `<form>` или `<main>`.
        drop_target_candidates = [
            "main form",
            "form",
            "[data-testid='composer']",
            "div[contenteditable='true']",
            "main",
            "body",
        ]

        result = await page.evaluate(
            """
            (args) => {
                const {filesData, candidates} = args;
                // Найти первого кандидата с непустым размером.
                let target = null;
                for (const sel of candidates) {
                    const el = document.querySelector(sel);
                    if (el && el.getBoundingClientRect().width > 0) {
                        target = el;
                        break;
                    }
                }
                if (!target) return {ok: false, error: 'no drop target found'};

                // Построить DataTransfer.
                let dt;
                try {
                    dt = new DataTransfer();
                } catch (e) {
                    return {ok: false, error: 'DataTransfer ctor failed: ' + e.message};
                }
                for (const fd of filesData) {
                    const bytes = atob(fd.b64);
                    const arr = new Uint8Array(bytes.length);
                    for (let i = 0; i < bytes.length; i++) {
                        arr[i] = bytes.charCodeAt(i);
                    }
                    const file = new File([arr], fd.name, {type: fd.mime});
                    dt.items.add(file);
                }

                const rect = target.getBoundingClientRect();
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;

                const fire = (type) => {
                    const ev = new DragEvent(type, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        clientX: cx,
                        clientY: cy,
                        dataTransfer: dt,
                    });
                    // Некоторые сборки Chromium не пробрасывают dataTransfer
                    // через конструктор DragEvent — подстраховываемся.
                    try {
                        Object.defineProperty(ev, 'dataTransfer', {
                            value: dt,
                            writable: false,
                        });
                    } catch (_) { /* ignore */ }
                    target.dispatchEvent(ev);
                };

                fire('dragenter');
                fire('dragover');
                fire('drop');

                return {
                    ok: true,
                    target: target.tagName.toLowerCase(),
                    files: filesData.map(f => f.name),
                };
            }
            """,
            {"filesData": files_data, "candidates": drop_target_candidates},
        )

        if not result or not result.get("ok"):
            raise RuntimeError(
                f"ChatGPT: drag-drop не удался: "
                f"{result.get('error') if result else 'no result'}"
            )
        logger.info(
            "ChatGPT: drag-drop dispatched на <{}> для файлов {}",
            result.get("target"),
            result.get("files"),
        )

    async def _wait_upload_done(self, *, timeout: float = 120) -> None:
        """Ждёт пока в композере исчезнут все upload-спиннеры.

        Эвристика: ищем элементы, у которых aria-label/role содержит
        loading/uploading/progress. Если за timeout сек спиннеры не пропали —
        логируем warning, но НЕ кидаем (превью может остаться, ChatGPT
        возможно перейдёт к обработке).
        """
        page = await self._page_ready()
        spinner_sels = [
            "form [role='progressbar']",
            "form [aria-busy='true']",
            "form [aria-label*='oading']",
            "form [aria-label*='ploading']",
            "form [data-testid*='loading']",
            "form [data-testid*='uploading']",
            "form svg.animate-spin",
            "form .animate-spin",
        ]
        deadline = asyncio.get_event_loop().time() + timeout
        last_count = -1
        while asyncio.get_event_loop().time() < deadline:
            total = 0
            for sel in spinner_sels:
                try:
                    total += await page.locator(sel).count()
                except Exception:  # noqa: BLE001
                    continue
            if total != last_count:
                logger.info("ChatGPT: upload-spinner count={}", total)
                last_count = total
            if total == 0:
                # ещё короткая пауза для устойчивости
                await asyncio.sleep(1.0)
                return
            await asyncio.sleep(1.0)
        logger.warning(
            "ChatGPT: upload-spinner так и не пропал за {}с — "
            "продолжаю, но upload может быть незавершён",
            timeout,
        )

    async def _dump_composer_html(self, *, max_chars: int = 4000) -> None:
        """Логирует outerHTML формы композера — для отладки селекторов
        скрепки/меню/input/превью при провалах аплоада."""
        try:
            page = await self._page_ready()
            html = await page.evaluate(
                """() => {
                    const form = document.querySelector('form') || document.body;
                    return form.outerHTML || '';
                }"""
            )
            html = (html or "").strip()
            if len(html) > max_chars:
                head = html[: max_chars // 2]
                tail = html[-max_chars // 2 :]
                html_log = (
                    f"{head}\n...[truncated {len(html) - max_chars} chars]...\n{tail}"
                )
            else:
                html_log = html
            logger.info("ChatGPT: composer outerHTML:\n{}", html_log)
        except Exception as e:  # noqa: BLE001
            logger.warning("ChatGPT: dump_composer_html упал: {}", e)

    async def _attach_file(self, file_path: Path) -> None:
        """Совместимая обёртка над `_attach_files` для одного файла."""
        await self._attach_files([file_path])

    async def ask_with_file(
        self,
        prompt: str,
        file_path: Path,
        *,
        timeout: float = 900,
    ) -> str:
        """В текущем чате прикрепляет файл, шлёт промт, возвращает текст ответа.

        Файл, который GPT может вернуть в ответ, скачивается отдельным методом
        `download_attachment_from_last_reply` после успешного ask_with_file.
        """
        return await self.ask_with_files(prompt, [file_path], timeout=timeout)

    async def ask_with_files(
        self,
        prompt: str,
        file_paths: list[Path],
        *,
        timeout: float = 900,
    ) -> str:
        """Аналогично `ask_with_file`, но прикрепляет НЕСКОЛЬКО файлов
        к одному сообщению, потом шлёт промт и возвращает текст ответа.
        """
        await self._attach_files(file_paths)
        # Сам текстовый промт + Send — переиспользуем существующий путь.
        await self._send_prompt(prompt)
        await self._wait_until_done(timeout=timeout)

        # Ждём стабилизации текста (как в обычном ask), но не строго — Code
        # Interpreter иногда генерирует файл и сразу отдаёт короткий текст.
        # Тайминги: poll 0.5 сек, стабильность 1.5 сек, deadline 30 сек.
        page = await self._page_ready()
        last_text = ""
        stable_for = 0.0
        deadline = asyncio.get_event_loop().time() + 30.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)
            text = await self._read_last_reply()
            still_generating = False
            for sel in STOP_BUTTON_SELECTORS:
                try:
                    if await page.locator(sel).count() > 0:
                        still_generating = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if still_generating:
                stable_for = 0.0
                last_text = text
                continue
            if text == last_text and len(text) > 0:
                stable_for += 0.5
                if stable_for >= 1.5:
                    break
            else:
                stable_for = 0.0
                last_text = text
        reply = await self._read_last_reply()
        logger.info("ChatGPT (file reply) len={}", len(reply))
        return reply

    async def _try_download_via_file_card(
        self, page: Page, *, timeout: float = 60,
    ) -> Download | None:
        """Пробует скачать файл, кликнув по карточке файла (behavior-btn).

        В новом ChatGPT UI (2025-Q2) клик по ``button.behavior-btn``
        напрямую запускает скачивание файла. Оборачиваем клик в
        ``page.expect_download`` чтобы перехватить Download-объект.

        Сначала ЖДЁМ появления карточки файла (polling до ``timeout``
        секунд), потом кликаем. Возвращает ``Download`` или ``None``.
        """
        # Ждём появления любого FILE_CARD_SELECTOR (polling).
        card_sel = await _first_matching(
            page, FILE_CARD_SELECTORS, timeout=timeout,
        )
        if card_sel is None:
            logger.info(
                "ChatGPT: _try_download_via_file_card: карточка файла "
                "не найдена за {} сек",
                timeout,
            )
            return None

        loc = page.locator(card_sel).first
        logger.info(
            "ChatGPT: пробую скачать файл кликом по карточке ({})",
            card_sel,
        )
        try:
            async with page.expect_download(timeout=30_000) as dl_info:
                await loc.click(timeout=5_000)
            dl: Download = await dl_info.value
            logger.info(
                "ChatGPT: download triggered via file card ({}), "
                "filename={}",
                card_sel, dl.suggested_filename,
            )
            return dl
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ChatGPT: клик по карточке ({}) не вызвал download: {}",
                card_sel, exc,
            )
            return None

    async def _hover_file_cards(self) -> None:
        """В новых сборках ChatGPT кнопка Download появляется только при
        наведении/клике по карточке файла. Делаем hover + click, чтобы
        активировать popover или кнопку скачивания (Radix trigger).
        """
        page = await self._page_ready()
        for sel in FILE_CARD_SELECTORS:
            try:
                cnt = await page.locator(sel).count()
                if cnt > 0:
                    loc = page.locator(sel).first
                    await loc.hover(timeout=2_000)
                    logger.info("ChatGPT: hover на карточке файла ({})", sel)
                    await asyncio.sleep(0.5)
                    # Radix trigger: hover может не открыть popover, нужен клик.
                    try:
                        await loc.click(timeout=2_000)
                        logger.info("ChatGPT: click на карточке файла ({})", sel)
                        await asyncio.sleep(1.0)
                    except Exception:  # noqa: BLE001
                        pass
                    return
            except Exception:  # noqa: BLE001
                continue

    async def _dump_last_assistant_html(self, *, max_chars: int = 4000) -> str:
        """Возвращает (и логирует) outerHTML последнего ответа ассистента —
        для отладки селекторов скачивания. Большие ответы обрезаются.

        Если ни один селектор не нашёл сообщение — дампим main/body, чтобы
        хоть что-то было для отладки.
        """
        page = await self._page_ready()
        html = await page.evaluate(
            """() => {
                const sels = [
                    "[data-message-author-role='assistant']",
                    "[data-author-role='assistant']",
                    "[data-message-author='assistant']",
                    "article[data-testid^='conversation-turn-']",
                ];
                for (const sel of sels) {
                    const msgs = document.querySelectorAll(sel);
                    if (msgs.length > 0) {
                        return msgs[msgs.length - 1].outerHTML || '';
                    }
                }
                // Fallback: ничего не нашли — дампим main/body.
                const main = document.querySelector('main') || document.body;
                return '[NO_ASSISTANT_FOUND] main/body=\\n' + (main.outerHTML || '');
            }"""
        )
        html = (html or "").strip()
        if len(html) > max_chars:
            head = html[: max_chars // 2]
            tail = html[-max_chars // 2 :]
            html_log = f"{head}\n...[truncated {len(html) - max_chars} chars]...\n{tail}"
        else:
            html_log = html
        logger.info("ChatGPT: last assistant outerHTML:\n{}", html_log)
        return html

    async def download_attachment_from_last_reply(
        self,
        target_path: Path,
        *,
        timeout: float = 900,
    ) -> Path:
        """Из последнего ответа ассистента ищет ссылку на скачивание файла,
        кликает по ней и сохраняет файл в `target_path`.

        Стратегия:
          1. Прямой клик по карточке файла (button.behavior-btn) с
             expect_download — в новом UI клик напрямую качает файл.
          2. Если карточки нет — поиск по DOWNLOAD_LINK_SELECTORS (до 60 сек).
          3. Если не нашли — hover/click по FILE_CARD_SELECTORS, потом ещё
             раз поиск уже с полным `timeout`.
          4. Если всё равно нет — dumpим outerHTML для отладки и RuntimeError.
        """
        page = await self._page_ready()
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Прямой клик по behavior-btn — скачивание через expect_download.
        # Ждём до 60 сек пока карточка файла появится в ответе
        # (ChatGPT иногда рендерит файл позже текста).
        download = await self._try_download_via_file_card(page, timeout=60)
        if download is not None:
            await download.save_as(str(target_path))
            size = target_path.stat().st_size if target_path.exists() else -1
            logger.info(
                "ChatGPT: файл скачан (behavior-btn) как {} "
                "(исходное имя {}, размер {} байт)",
                target_path, download.suggested_filename, size,
            )
            if size < 1024:
                logger.warning(
                    "ChatGPT: размер подозрительно мал ({} байт).", size,
                )
                await self._dump_last_assistant_html()
            return target_path

        # 2. Классический путь — ищем ссылку/кнопку скачивания.
        link_sel = await _first_matching(page, DOWNLOAD_LINK_SELECTORS, timeout=60)
        if not link_sel:
            await self._hover_file_cards()
            link_sel = await _first_matching(
                page, DOWNLOAD_LINK_SELECTORS, timeout=timeout,
            )
        if not link_sel:
            await self._dump_last_assistant_html()
            raise RuntimeError(
                "ChatGPT: ссылка на скачивание не найдена в ответе. "
                "Полный outerHTML последнего ответа залогирован — пришли строки "
                "из консоли с 'last assistant outerHTML' разработчику."
            )

        logger.info("ChatGPT: жму на ссылку скачивания {}", link_sel)
        try:
            async with page.expect_download(timeout=timeout * 1000) as dl_info:
                await page.locator(link_sel).first.click()
            download = await dl_info.value
        except Exception as e:  # noqa: BLE001
            await self._dump_last_assistant_html()
            raise RuntimeError(f"ChatGPT: не удалось скачать файл: {e}") from e

        await download.save_as(str(target_path))
        size = target_path.stat().st_size if target_path.exists() else -1
        logger.info(
            "ChatGPT: файл скачан как {} (исходное имя {}, размер {} байт)",
            target_path,
            download.suggested_filename,
            size,
        )
        if size < 1024:
            # Логируем outerHTML, чтобы понять почему скачался «крошечный»
            # файл (часто это svg-иконка из карточки share/edit, а не сам xlsx).
            logger.warning(
                "ChatGPT: размер скачанного файла подозрительно мал ({} байт). "
                "Дампим outerHTML последнего ответа для отладки.",
                size,
            )
            await self._dump_last_assistant_html()
        return target_path
