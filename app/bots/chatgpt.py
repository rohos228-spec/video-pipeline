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
# Кнопка-парбяг (paperclip) — нужна, чтобы триггернуть появление input[type=file],
# который ChatGPT иногда создаёт лениво только после клика.
ATTACH_BUTTON_SELECTORS = [
    "button[aria-label='Attach files']",
    "button[aria-label*='Attach']",
    "button[aria-label='Прикрепить файлы']",
    "button[aria-label*='Прикрепить']",
    "button[data-testid='composer-attach-files-button']",
    "button[data-testid='composer-attach-button']",
]
# Превью прикреплённого файла в прометной панели — индикатор успешной загрузки.
FILE_PREVIEW_SELECTORS = [
    "div[data-testid*='file-preview']",
    "div[data-testid*='attachment']",
    "[data-testid='composer-file-attachment']",
    "div.group\\/attachment",
    "div[role='button'][aria-label*='Remove']",
]
# Ссылка на скачивание сгенерированного файла в ответе ассистента.
DOWNLOAD_LINK_SELECTORS = [
    "[data-message-author-role='assistant']:last-of-type a[download]",
    "[data-message-author-role='assistant']:last-of-type a[href*='/files/']",
    "[data-message-author-role='assistant']:last-of-type a[href*='sandbox']",
    "[data-message-author-role='assistant']:last-of-type a[href*='.xlsx']",
]

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
        await asyncio.sleep(0.3)

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
        """Отправить один промт в текущий чат и вернуть финальный ответ.

        После того как кнопка «Stop generating» пропала, ждём пока текст
        стабилизируется (не меняется 6 сек подряд), но не дольше 120 сек.
        ChatGPT 5 thinking model часто продолжает рендерить ответ ещё
        несколько десятков секунд после исчезновения кнопки stop — раньше
        мы хватали обрезанную версию.
        """
        await self._send_prompt(prompt)
        await self._wait_until_done(timeout=timeout)

        # Ждём стабилизации текста: не меняется 6 сек подряд, не дольше 120с total
        page = await self._page_ready()
        last_text = ""
        stable_for = 0.0
        deadline = asyncio.get_event_loop().time() + 120.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)
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
                stable_for += 1.0
                if stable_for >= 6.0:
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

    async def _attach_file(self, file_path: Path) -> None:
        """Загружает файл в текущий черновик сообщения через скрытый input[type=file].
        Перед этим может потребоваться кликнуть по кнопке-скрепке, чтобы input
        появился в DOM. Ждёт превью прикреплённого файла как подтверждение.
        """
        page = await self._page_ready()
        await self._dismiss_no_auth_modal(page)

        if not file_path.exists():
            raise FileNotFoundError(f"upload: файл не найден {file_path}")

        # 1. Пытаемся найти input[type=file] напрямую.
        input_sel = await _first_matching(page, FILE_INPUT_SELECTORS, timeout=2)
        # 2. Если не нашли — кликаем по скрепке.
        if not input_sel:
            attach_sel = await _first_matching(page, ATTACH_BUTTON_SELECTORS, timeout=5)
            if attach_sel:
                try:
                    await page.locator(attach_sel).first.click(timeout=3_000)
                    await asyncio.sleep(0.5)
                except Exception as e:  # noqa: BLE001
                    logger.warning("ChatGPT: не смог кликнуть скрепку {}: {}", attach_sel, e)
            input_sel = await _first_matching(page, FILE_INPUT_SELECTORS, timeout=8)

        if not input_sel:
            raise RuntimeError(
                "ChatGPT: не нашёл input[type=file]. Возможно изменился UI — "
                "обнови FILE_INPUT_SELECTORS / ATTACH_BUTTON_SELECTORS."
            )

        # set_input_files работает даже со скрытыми input.
        await page.locator(input_sel).first.set_input_files(str(file_path))
        logger.info("ChatGPT: загружаю файл {} через {}", file_path.name, input_sel)

        # Ждём появление превью + завершение фоновой загрузки.
        preview_sel = await _first_matching(page, FILE_PREVIEW_SELECTORS, timeout=30)
        if preview_sel:
            logger.info("ChatGPT: превью файла появилось ({})", preview_sel)
        else:
            logger.warning(
                "ChatGPT: превью файла не появилось за 30 сек — продолжаю на свой страх"
            )
        # Дополнительная пауза, чтобы upload точно завершился.
        await asyncio.sleep(2.0)

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
        await self._attach_file(file_path)
        # Сам текстовый промт + Send — переиспользуем существующий путь.
        await self._send_prompt(prompt)
        await self._wait_until_done(timeout=timeout)

        # Ждём стабилизации текста (как в обычном ask), но не строго — Code
        # Interpreter иногда генерирует файл и сразу отдаёт короткий текст.
        page = await self._page_ready()
        last_text = ""
        stable_for = 0.0
        deadline = asyncio.get_event_loop().time() + 120.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)
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
                stable_for += 1.0
                if stable_for >= 4.0:
                    break
            else:
                stable_for = 0.0
                last_text = text
        reply = await self._read_last_reply()
        logger.info("ChatGPT (file reply) len={}", len(reply))
        return reply

    async def download_attachment_from_last_reply(
        self,
        target_path: Path,
        *,
        timeout: float = 60,
    ) -> Path:
        """Из последнего ответа ассистента ищет ссылку на скачивание файла,
        кликает по ней и сохраняет файл в `target_path`.

        Возвращает путь к скачанному файлу. Бросает RuntimeError, если ссылка
        не найдена или скачивание не удалось.
        """
        page = await self._page_ready()
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        link_sel = await _first_matching(page, DOWNLOAD_LINK_SELECTORS, timeout=15)
        if not link_sel:
            # Иногда ссылка появляется не сразу (Code Interpreter ещё пишет файл).
            # Подождём подольше.
            link_sel = await _first_matching(page, DOWNLOAD_LINK_SELECTORS, timeout=timeout)
        if not link_sel:
            raise RuntimeError(
                "ChatGPT: ссылка на скачивание не найдена в ответе. "
                "Проверь, что GPT действительно вернул файл."
            )

        logger.info("ChatGPT: жму на ссылку скачивания {}", link_sel)
        try:
            async with page.expect_download(timeout=timeout * 1000) as dl_info:
                await page.locator(link_sel).first.click()
            download: Download = await dl_info.value
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"ChatGPT: не удалось скачать файл: {e}") from e

        await download.save_as(str(target_path))
        logger.info(
            "ChatGPT: файл скачан как {} (исходное имя {})",
            target_path,
            download.suggested_filename,
        )
        return target_path
