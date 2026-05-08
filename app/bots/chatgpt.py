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
]
# Карточка файла как таковая — иногда нужно сначала открыть её
# (двойной клик / hover), чтобы появилась кнопка Download.
FILE_CARD_SELECTORS = [
    f"{ASSISTANT_LAST_PREFIX} [data-testid*='file']",
    f"{ASSISTANT_LAST_PREFIX} [data-testid*='attachment']",
    f"{ASSISTANT_LAST_PREFIX} div[role='button']:has(svg)",
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

    async def _attach_files(self, file_paths: list[Path]) -> None:
        """Загружает один или несколько файлов в текущий черновик сообщения
        через скрытый input[type=file] (он `multiple`).

        Стратегия (на 2025-Q4 ChatGPT использует поповер-меню под скрепкой):
          1. Если input[type=file] уже есть в DOM — используем его.
          2. Иначе кликаем по кнопке-скрепке/«+» (ATTACH_BUTTON_SELECTORS).
             Если открылось поповер-меню — кликаем по пункту
             «Add photos and files» (ATTACH_MENU_ITEM_SELECTORS), это
             материализует input[type=file] в DOM.
          3. Снова ищем input[type=file] и шлём `set_input_files`.
          4. Жёстко ждём превью (FILE_PREVIEW_SELECTORS) до `preview_timeout`.
             Если за это время превью не появилось — кидаем RuntimeError
             с диагностикой. ВАЖНО: НЕ продолжаем «на свой страх», иначе
             промт уйдёт в ChatGPT без файла и юзер получит мусорный ответ.
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

        # 1. Пытаемся найти input[type=file] напрямую.
        input_sel = await _first_matching(page, FILE_INPUT_SELECTORS, timeout=2)
        if input_sel:
            logger.info("ChatGPT: input[type=file] найден сразу ({})", input_sel)

        # 2. Если не нашли — кликаем по скрепке, потом, если открылось
        #    поповер-меню, по пункту «Add photos and files».
        if not input_sel:
            attach_sel = await _first_matching(page, ATTACH_BUTTON_SELECTORS, timeout=10)
            if not attach_sel:
                await self._dump_composer_html()
                raise RuntimeError(
                    "ChatGPT: не нашёл кнопку-скрепку (ATTACH_BUTTON_SELECTORS). "
                    "Возможно изменился UI — пришли скрин окна Chrome или "
                    "посмотри в консоли строку 'composer outerHTML'."
                )
            logger.info("ChatGPT: кликаю по скрепке ({})", attach_sel)
            try:
                await page.locator(attach_sel).first.click(timeout=3_000)
                await asyncio.sleep(0.6)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ChatGPT: не смог кликнуть скрепку {}: {}", attach_sel, e
                )

            # 2a. Если появилось поповер-меню — кликаем «Add photos and files».
            menu_sel = await _first_matching(page, ATTACH_MENU_ITEM_SELECTORS, timeout=2)
            if menu_sel:
                logger.info(
                    "ChatGPT: кликаю по пункту меню '{}'", menu_sel
                )
                try:
                    await page.locator(menu_sel).first.click(timeout=3_000)
                    await asyncio.sleep(0.4)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "ChatGPT: не смог кликнуть пункт меню {}: {}", menu_sel, e
                    )

            input_sel = await _first_matching(page, FILE_INPUT_SELECTORS, timeout=10)

        if not input_sel:
            await self._dump_composer_html()
            raise RuntimeError(
                "ChatGPT: не нашёл input[type=file] даже после клика по скрепке. "
                "Возможно изменился UI — пришли скрин окна Chrome или строки "
                "'composer outerHTML' из консоли."
            )

        # 3. set_input_files работает даже со скрытыми input. Берём ВСЕ
        #    подходящие input-ы и шлём в первый видимый/последний (новые
        #    input-ы добавляются последними).
        loc = page.locator(input_sel).last
        await loc.set_input_files([str(p) for p in file_paths])
        logger.info("ChatGPT: set_input_files выполнен через {}", input_sel)

        # 4. Жёстко ждём превью. Для xlsx upload в ChatGPT может занимать
        #    до ~30-60 сек (особенно с Code Interpreter). Если превью так
        #    и не появилось — это значит что аплоад провалился (или ChatGPT
        #    отверг файл из-за лимита размера/типа). НЕ шлём промт.
        preview_timeout = 60.0
        preview_sel = await _first_matching(
            page, FILE_PREVIEW_SELECTORS, timeout=preview_timeout
        )
        if not preview_sel:
            await self._dump_composer_html()
            raise RuntimeError(
                f"ChatGPT: за {int(preview_timeout)} сек после set_input_files "
                f"не появилось превью файла(ов) [{names}]. "
                "Аплоад НЕ удался — промт в ChatGPT не отправляю. "
                "Проверь окно Chrome: видна ли карточка файла под полем ввода. "
                "Если нет — возможно ChatGPT отверг файл (формат/размер) или "
                "изменился UI (FILE_PREVIEW_SELECTORS устарели)."
            )
        logger.info("ChatGPT: превью файла(ов) появилось ({})", preview_sel)

        # 5. Дополнительно проверяем: input.files действительно содержит
        #    наши файлы. Это страхует от случая, когда set_input_files
        #    «прошёл», а ChatGPT отверг файл и убрал input из DOM.
        try:
            file_count = await page.locator(input_sel).last.evaluate(
                "el => (el.files ? el.files.length : 0)"
            )
            logger.info("ChatGPT: input.files.length = {}", file_count)
            if file_count == 0:
                await self._dump_composer_html()
                raise RuntimeError(
                    "ChatGPT: input.files пуст после set_input_files — "
                    "видимо ChatGPT отверг файл. Проверь размер/формат."
                )
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("ChatGPT: не смог прочитать input.files: {}", e)

        # 6. Дополнительная пауза, чтобы upload точно завершился (для
        #    нескольких файлов даём больше времени).
        await asyncio.sleep(2.0 + 1.0 * (len(file_paths) - 1))

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

    async def _hover_file_cards(self) -> None:
        """В новых сборках ChatGPT кнопка Download появляется только при
        наведении/клике по карточке файла. Делаем hover, чтобы её активировать.
        """
        page = await self._page_ready()
        for sel in FILE_CARD_SELECTORS:
            try:
                cnt = await page.locator(sel).count()
                if cnt > 0:
                    await page.locator(sel).first.hover(timeout=2_000)
                    logger.info("ChatGPT: hover на карточке файла ({})", sel)
                    await asyncio.sleep(0.5)
                    return
            except Exception:  # noqa: BLE001
                continue

    async def _dump_last_assistant_html(self, *, max_chars: int = 4000) -> str:
        """Возвращает (и логирует) outerHTML последнего ответа ассистента —
        для отладки селекторов скачивания. Большие ответы обрезаются."""
        page = await self._page_ready()
        html = await page.evaluate(
            """() => {
                const msgs = document.querySelectorAll("[data-message-author-role='assistant']");
                if (msgs.length === 0) return '';
                return msgs[msgs.length - 1].outerHTML || '';
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
        timeout: float = 60,
    ) -> Path:
        """Из последнего ответа ассистента ищет ссылку на скачивание файла,
        кликает по ней и сохраняет файл в `target_path`.

        Стратегия:
          1. Прямой поиск по DOWNLOAD_LINK_SELECTORS.
          2. Если не нашли — hover по карточке файла, потом ещё раз поиск.
          3. Если всё равно нет — dumpим outerHTML последнего ответа для отладки
             и кидаем RuntimeError.
        """
        page = await self._page_ready()
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        link_sel = await _first_matching(page, DOWNLOAD_LINK_SELECTORS, timeout=15)
        if not link_sel:
            # 2. Возможно нужен hover.
            await self._hover_file_cards()
            link_sel = await _first_matching(page, DOWNLOAD_LINK_SELECTORS, timeout=timeout)
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
            download: Download = await dl_info.value
        except Exception as e:  # noqa: BLE001
            # На всякий случай дампим HTML, чтобы понять что было вместо файла.
            await self._dump_last_assistant_html()
            raise RuntimeError(f"ChatGPT: не удалось скачать файл: {e}") from e

        await download.save_as(str(target_path))
        logger.info(
            "ChatGPT: файл скачан как {} (исходное имя {})",
            target_path,
            download.suggested_filename,
        )
        return target_path
