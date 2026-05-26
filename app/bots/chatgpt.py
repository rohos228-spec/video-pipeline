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
import re
from pathlib import Path

from loguru import logger
from playwright.async_api import Download, Page

from app.bots.browser import BrowserSession

CHATGPT_URL = "https://chatgpt.com/"

# Идентификатор логики attach/send — показывается в /api/studio-version.
# Если в UI v69, а backend_attach другой — Python не перезапущен после git pull.
CHATGPT_ATTACH_LOGIC_ID = "send-wait-enabled-v77"

# ChatGPT при повторном drop одного имени добавляет суффикс: frame_001.png → frame_001(3).png
_ATTACHMENT_DEDUP_SUFFIX = re.compile(r"\(\d+\)")


def attachment_name_visible_in_text(expected_filename: str, composer_text: str) -> bool:
    """Имя файла видно в композере (точное или с суффиксом (N) от ChatGPT)."""
    if expected_filename in composer_text:
        return True
    stem = Path(expected_filename).stem
    suffix = Path(expected_filename).suffix
    if not stem:
        return False
    pattern = (
        re.escape(stem)
        + r"(?:"
        + _ATTACHMENT_DEDUP_SUFFIX.pattern
        + r")?"
        + re.escape(suffix)
    )
    return bool(re.search(pattern, composer_text, re.IGNORECASE))


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

    async def _count_user_messages(self) -> int:
        page = await self._page_ready()
        n = await page.evaluate(
            """() => {
                return document.querySelectorAll(
                    "[data-message-author-role='user'], [data-author-role='user']"
                ).length;
            }"""
        )
        return int(n or 0)

    async def _composer_draft_text(self) -> str:
        """Текст в поле ввода композера (без подписей к вложениям)."""
        page = await self._page_ready()
        text = await page.evaluate(
            """() => {
                const el = document.querySelector(
                    "div#prompt-textarea[contenteditable='true']"
                ) || document.querySelector("textarea#prompt-textarea")
                || document.querySelector("div[contenteditable='true'][data-id='root']");
                if (!el) return "";
                if (el.tagName === "TEXTAREA") return el.value || "";
                return el.innerText || "";
            }"""
        )
        return (text or "").strip()

    async def _is_send_button_enabled(self, page: Page) -> tuple[str | None, bool]:
        """Первая кнопка Send в композере и enabled (не disabled)."""
        for sel in SEND_BUTTON_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                disabled = await loc.get_attribute("disabled")
                aria = (await loc.get_attribute("aria-disabled") or "").lower()
                enabled = disabled is None and aria not in ("true", "1")
                if enabled:
                    return sel, True
            except Exception:  # noqa: BLE001
                continue
        return None, False

    async def _wait_send_button_enabled(
        self, page: Page, *, timeout: float = 45
    ) -> str:
        """Ждём активную кнопку Send (после ввода текста / загрузки файлов)."""
        deadline = asyncio.get_event_loop().time() + timeout
        last_log = 0.0
        while asyncio.get_event_loop().time() < deadline:
            sel, ok = await self._is_send_button_enabled(page)
            if ok and sel:
                return sel
            now = asyncio.get_event_loop().time()
            if now - last_log > 3.0:
                logger.info("ChatGPT: жду активную кнопку Send…")
                last_log = now
            await asyncio.sleep(0.35)
        await self._dump_composer_html()
        await self._dump_send_button_candidates(page)
        raise RuntimeError(
            "ChatGPT: кнопка Send не стала активной — "
            "текст/файлы не приняты композером"
        )

    async def _verify_message_dispatched(
        self,
        page: Page,
        *,
        user_msgs_before: int,
        had_draft: bool,
        timeout: float = 20,
    ) -> None:
        """Проверяем, что сообщение реально ушло (не зависло в черновике)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for sel in STOP_BUTTON_SELECTORS:
                try:
                    if await page.locator(sel).count() > 0:
                        logger.info("ChatGPT: отправка подтверждена (Stop generating)")
                        return
                except Exception:  # noqa: BLE001
                    pass
            if await self._count_user_messages() > user_msgs_before:
                logger.info("ChatGPT: отправка подтверждена (новое user-сообщение)")
                return
            draft = await self._composer_draft_text()
            if had_draft and len(draft) < 8:
                logger.info("ChatGPT: отправка подтверждена (композер очищен)")
                return
            if not had_draft:
                # Только файлы — черновик мог быть пустым; ждём user-msg или stop
                pass
            await asyncio.sleep(0.4)
        draft_left = await self._composer_draft_text()
        raise RuntimeError(
            "ChatGPT: сообщение не отправилось — черновик остался в композере "
            f"({len(draft_left)} симв.), user-msgs {user_msgs_before}→"
            f"{await self._count_user_messages()}"
        )

    async def _dispatch_composer_send(
        self,
        page: Page,
        *,
        had_draft: bool,
        send_timeout: float = 45,
        verify_timeout: float = 25,
    ) -> None:
        """Клик по активной Send + проверка (как в рабочем TG/xlsx-flow)."""
        await self._dismiss_no_auth_modal(page)
        user_before = await self._count_user_messages()
        send_sel = await self._wait_send_button_enabled(page, timeout=send_timeout)
        btn = page.locator(send_sel).first
        logger.info("ChatGPT: Send активна ({}) — клик", send_sel)
        try:
            await btn.click(timeout=8_000)
        except Exception as e:  # noqa: BLE001
            logger.warning("ChatGPT: обычный клик Send упал ({}), force-click", e)
            await btn.click(timeout=8_000, force=True)
        try:
            await self._verify_message_dispatched(
                page,
                user_msgs_before=user_before,
                had_draft=had_draft,
                timeout=verify_timeout,
            )
            return
        except RuntimeError as e:
            logger.warning("ChatGPT: Send-клик без эффекта ({}), пробую Enter", e)
        input_sel = await _first_matching(page, INPUT_SELECTORS, timeout=5)
        if input_sel:
            await page.locator(input_sel).first.focus()
        await page.keyboard.press("Enter")
        await self._verify_message_dispatched(
            page,
            user_msgs_before=user_before,
            had_draft=had_draft,
            timeout=verify_timeout,
        )

    async def _fill_composer_text(self, page: Page, text: str, input_sel: str) -> None:
        """Ввод текста в ProseMirror / textarea (как в раннем боте + insertText)."""
        locator = page.locator(input_sel).first
        await locator.click()
        await locator.focus()
        stripped = (text or "").strip()
        if not stripped:
            return
        if len(stripped) > 8000:
            await page.evaluate(
                """([sel, t]) => {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    if (el.tagName === "TEXTAREA") {
                        el.focus();
                        el.value = t;
                        el.dispatchEvent(new Event("input", { bubbles: true }));
                    } else {
                        el.focus();
                        el.innerText = t;
                        el.dispatchEvent(
                            new InputEvent("input", { bubbles: true, data: t })
                        );
                    }
                }""",
                [input_sel, stripped],
            )
        else:
            await page.keyboard.insert_text(stripped)
        await asyncio.sleep(0.6)

    async def _click_send(self) -> None:
        """Нажать Send без ввода текста в композер (только вложения)."""
        page = await self._page_ready()
        await self._dispatch_composer_send(page, had_draft=False, send_timeout=60)

    async def _count_attachment_previews(self) -> int:
        """Сколько превью вложений сейчас в композере."""
        page = await self._page_ready()
        count = await page.evaluate(
            """() => {
                const form = document.querySelector('main form')
                    || document.querySelector('form[data-type="unified-composer"]')
                    || document.querySelector('form');
                if (!form) return 0;
                const removeBtns = form.querySelectorAll(
                    "button[aria-label*='Remove file'], "
                    + "button[aria-label*='Удалить файл']"
                );
                if (removeBtns.length > 0) return removeBtns.length;
                return form.querySelectorAll(
                    "[data-testid*='file-preview'], "
                    + "[data-testid*='attachment'], "
                    + "[data-testid='composer-file-attachment']"
                ).length;
            }"""
        )
        return int(count or 0)

    async def _composer_attachment_text(self) -> str:
        """Текст композера — для проверки, что имена файлов реально видны."""
        page = await self._page_ready()
        text = await page.evaluate(
            """() => {
                const form = document.querySelector('main form')
                    || document.querySelector('form[data-type="unified-composer"]')
                    || document.querySelector('form');
                return form ? (form.innerText || '') : '';
            }"""
        )
        return (text or "").strip()

    async def _composer_attachment_labels(self) -> str:
        """aria-label плиток вложений — ChatGPT часто дублирует имя как name(2).png."""
        page = await self._page_ready()
        labels = await page.evaluate(
            """() => {
                const form = document.querySelector('main form')
                    || document.querySelector('form[data-type="unified-composer"]')
                    || document.querySelector('form');
                if (!form) return '';
                const parts = [];
                for (const el of form.querySelectorAll('[role="group"][aria-label]')) {
                    const lb = el.getAttribute('aria-label');
                    if (lb) parts.push(lb);
                }
                for (const btn of form.querySelectorAll(
                    "button[aria-label*='Remove file'], button[aria-label*='Удалить файл']"
                )) {
                    const lb = btn.getAttribute('aria-label') || '';
                    parts.push(lb);
                }
                return parts.join('\\n');
            }"""
        )
        return (labels or "").strip()

    async def _files_visible_in_composer(self, file_paths: list[Path]) -> bool:
        text = await self._composer_attachment_text()
        labels = await self._composer_attachment_labels()
        haystack = f"{text}\n{labels}".strip()
        if not haystack:
            return False
        return all(attachment_name_visible_in_text(fp.name, haystack) for fp in file_paths)

    async def _clear_composer_attachments(self) -> int:
        """Удаляет все черновые вложения из композера (перед новым batch-upload)."""
        page = await self._page_ready()
        removed = 0
        for _ in range(40):
            count = await page.evaluate(
                """() => {
                    const form = document.querySelector('main form')
                        || document.querySelector('form[data-type="unified-composer"]')
                        || document.querySelector('form');
                    if (!form) return 0;
                    return form.querySelectorAll(
                        "button[aria-label*='Remove file'], "
                        + "button[aria-label*='Удалить файл']"
                    ).length;
                }"""
            )
            if not count:
                break
            btn = page.locator(
                "form button[aria-label*='Удалить файл'], "
                "form button[aria-label*='Remove file']"
            ).last
            try:
                await btn.click(timeout=3_000)
                removed += 1
                await asyncio.sleep(0.35)
            except Exception as e:  # noqa: BLE001
                logger.warning("ChatGPT: не удалось снять вложение: {}", e)
                break
        if removed:
            logger.info("ChatGPT: очищено {} черновых вложений в композере", removed)
            await asyncio.sleep(0.4)
        return removed

    async def _send_prompt(self, text: str, *, clear_first: bool = True) -> None:
        page = await self._page_ready()
        await self._dismiss_no_auth_modal(page)
        input_sel = await _first_matching(page, INPUT_SELECTORS, timeout=30)
        if not input_sel:
            raise RuntimeError("ChatGPT: не найден input для промта")

        locator = page.locator(input_sel).first
        await locator.click()
        await locator.focus()
        # После прикрепления файлов Ctrl+A снимает вложения — не чистим.
        if clear_first:
            try:
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
            except Exception:  # noqa: BLE001
                pass
        stripped = (text or "").strip()
        if stripped:
            await self._fill_composer_text(page, stripped, input_sel)
        if not clear_first:
            n_att = await self._count_attachment_previews()
            logger.info(
                "ChatGPT: после ввода текста превью вложений={} (clear_first=False)",
                n_att,
            )
        logger.info(
            "ChatGPT: текст в композере ({} симв.), отправляю через активную Send",
            len(stripped),
        )
        await self._dispatch_composer_send(
            page,
            had_draft=bool(stripped),
            send_timeout=60 if not clear_first else 45,
        )

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

    async def _wait_for_generation_started(
        self,
        *,
        timeout: float = 45,
        project_id: int | None = None,
    ) -> None:
        """После Send: ждём Stop или новый ответ — иначе сообщение не ушло."""
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        page = await self._page_ready()
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
            for sel in STOP_BUTTON_SELECTORS:
                try:
                    if await page.locator(sel).count() > 0:
                        return
                except Exception:  # noqa: BLE001
                    continue
            await sleep_cancellable(0.4, project_id)
        raise TimeoutError(
            "ChatGPT: после отправки нет индикатора генерации (Stop) — "
            "сообщение, вероятно, не ушло"
        )

    async def _wait_until_done(
        self, *, timeout: float = 300, project_id: int | None = None
    ) -> None:
        """Ждём, пока пропадёт кнопка "Stop generating"."""
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        await self._wait_for_generation_started(
            timeout=min(45.0, timeout * 0.25),
            project_id=project_id,
        )
        page = await self._page_ready()
        deadline = asyncio.get_event_loop().time() + timeout
        await sleep_cancellable(0.8, project_id)
        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
            still_generating = False
            for sel in STOP_BUTTON_SELECTORS:
                try:
                    if await page.locator(sel).count() > 0:
                        still_generating = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not still_generating:
                await sleep_cancellable(1.5, project_id)
                return
            await sleep_cancellable(0.5, project_id)
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

    async def ask(
        self, prompt: str, *, timeout: float = 300, project_id: int | None = None
    ) -> str:
        """Отправить один промт в текущий чат и вернуть финальный ответ.

        После того как кнопка «Stop generating» пропала, ждём пока текст
        стабилизируется (не меняется 6 сек подряд), но не дольше 120 сек.
        ChatGPT 5 thinking model часто продолжает рендерить ответ ещё
        несколько десятков секунд после исчезновения кнопки stop — раньше
        мы хватали обрезанную версию.
        """
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        abort_if_cancelled(project_id)
        await self._send_prompt(prompt)
        abort_if_cancelled(project_id)
        await self._wait_until_done(timeout=timeout, project_id=project_id)

        # Ждём стабилизации текста: не меняется 6 сек подряд, не дольше 120с total
        page = await self._page_ready()
        last_text = ""
        stable_for = 0.0
        deadline = asyncio.get_event_loop().time() + 120.0
        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
            await sleep_cancellable(1.0, project_id)
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

    async def ask_fresh(
        self, prompt: str, *, timeout: float = 300, project_id: int | None = None
    ) -> str:
        """Новый чат + один промт + ответ."""
        from app.services.step_cancel import abort_if_cancelled

        abort_if_cancelled(project_id)
        await self.new_conversation()
        abort_if_cancelled(project_id)
        return await self.ask(prompt, timeout=timeout, project_id=project_id)

    # ---------- File upload / download (для xlsx-пайплайна) -------------------

    async def _materialize_file_input(self, *, fresh: bool = False) -> str:
        """Скрепка → пункт меню → input[type=file] (как ручной аплоад).

        fresh=True — всегда открыть меню заново (нужно для batch/multi-file:
        иначе второй set_input_files попадает в старый input и «ломает» первый
        файл — в UI вечная загрузка, хотя имя уже видно).
        """
        page = await self._page_ready()
        if not fresh:
            input_sel = await _first_matching(page, FILE_INPUT_SELECTORS, timeout=2)
            if input_sel:
                return input_sel

        attach_sel = await _first_matching(page, ATTACH_BUTTON_SELECTORS, timeout=12)
        if not attach_sel:
            await self._dump_composer_html()
            raise RuntimeError(
                "ChatGPT: не найдена кнопка-скрепка (ATTACH_BUTTON_SELECTORS)"
            )
        logger.info("ChatGPT: кликаю скрепку ({})", attach_sel)
        await page.locator(attach_sel).first.click(timeout=5_000)
        await asyncio.sleep(0.7)

        menu_sel = await _first_matching(page, ATTACH_MENU_ITEM_SELECTORS, timeout=4)
        if menu_sel:
            logger.info("ChatGPT: кликаю пункт меню вложений ({})", menu_sel)
            await page.locator(menu_sel).first.click(timeout=5_000)
            await asyncio.sleep(0.5)

        input_sel = await _first_matching(page, FILE_INPUT_SELECTORS, timeout=12)
        if not input_sel:
            await self._dump_composer_html()
            raise RuntimeError(
                "ChatGPT: после скрепки не появился input[type=file]"
            )
        return input_sel

    async def _fire_file_input_events(self, input_sel: str) -> None:
        """React ChatGPT иногда не видит файлы без input/change после set_input_files."""
        page = await self._page_ready()
        try:
            await page.locator(input_sel).last.evaluate(
                """el => {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("ChatGPT: fire input/change на file input: {}", e)

    async def _attachments_upload_state(self, expected: int) -> dict[str, int | str | bool]:
        """Сколько вложений в композере и есть ли у них индикатор загрузки."""
        page = await self._page_ready()
        raw = await page.evaluate(
            """(expected) => {
                const form = document.querySelector('main form')
                    || document.querySelector('form[data-type="unified-composer"]')
                    || document.querySelector('form');
                if (!form) return {ok: false, count: 0, loading: 0, reason: 'no form'};
                const removeBtns = form.querySelectorAll(
                    "button[aria-label*='Remove file'], "
                    + "button[aria-label*='Удалить файл']"
                );
                let count = removeBtns.length;
                if (count === 0) {
                    count = form.querySelectorAll(
                        "[data-testid*='file-preview'], "
                        + "[data-testid*='attachment'], "
                        + "[data-testid='composer-file-attachment']"
                    ).length;
                }
                const loaderSels = [
                    "[data-testid*='attachment'] [role='progressbar']",
                    "[data-testid*='file-preview'] [role='progressbar']",
                    "[data-testid*='attachment'] .animate-spin",
                    "[data-testid*='file-preview'] .animate-spin",
                    "[data-testid*='uploading']",
                    "[aria-label*='ploading']",
                    "[aria-busy='true']",
                ];
                let loading = 0;
                for (const sel of loaderSels) {
                    for (const el of form.querySelectorAll(sel)) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) loading += 1;
                    }
                }
                return {
                    ok: count >= expected && loading === 0,
                    count,
                    loading,
                    reason: count < expected ? 'count' : (loading ? 'loading' : 'ready'),
                };
            }""",
            expected,
        )
        return dict(raw or {"ok": False, "count": 0, "loading": 0, "reason": "eval"})

    async def _wait_attachments_ready(
        self, file_paths: list[Path], *, timeout: float = 120
    ) -> None:
        """Ждём имена файлов в композере и исчезновение спиннеров на вложениях."""
        expected = len(file_paths)
        deadline = asyncio.get_event_loop().time() + timeout
        last_log = ""
        while asyncio.get_event_loop().time() < deadline:
            names_ok = await self._files_visible_in_composer(file_paths)
            state = await self._attachments_upload_state(expected)
            count = int(state.get("count") or 0)
            loading = int(state.get("loading") or 0)
            reason = state.get("reason", "?")
            count_ok = count >= expected
            log_key = f"{names_ok}:{reason}:{count}:{loading}"
            if log_key != last_log:
                logger.info(
                    "ChatGPT: upload state names_ok={} count={}/{} loading={} ({})",
                    names_ok,
                    count,
                    expected,
                    loading,
                    reason,
                )
                last_log = log_key
            if names_ok and count_ok and loading == 0:
                await asyncio.sleep(0.8)
                return
            await asyncio.sleep(0.5)
        await self._dump_composer_html()
        raise RuntimeError(
            f"ChatGPT: вложения не готовы за {timeout:.0f}с "
            f"[{', '.join(p.name for p in file_paths)}]"
        )

    async def _attach_batch_via_paperclip(self, file_paths: list[Path]) -> None:
        """Один раз скрепка → set_input_files(all) — как ручной multi-select."""
        page = await self._page_ready()
        input_sel = await self._materialize_file_input(fresh=True)
        paths = [str(p) for p in file_paths]
        logger.info(
            "ChatGPT: paperclip batch set_input_files {} файлов через {}",
            len(paths),
            input_sel,
        )
        await page.locator(input_sel).last.set_input_files(paths)
        await self._fire_file_input_events(input_sel)
        await self._wait_attachments_ready(file_paths, timeout=120)

    async def _attach_one_via_paperclip(self, file_path: Path) -> None:
        """Прикрепить один файл через скрепку + set_input_files."""
        await self._attach_batch_via_paperclip([file_path])

    async def _attach_files(self, file_paths: list[Path]) -> None:
        """Загружает один или несколько файлов в текущий черновик сообщения.

        Стратегия (как ручной аплоад мышкой в Chrome):
          1. Главный путь — drag-and-drop эмуляция. В странице конструируется
             File-объект, кладётся в DataTransfer, диспатчатся события
             dragenter/dragover/drop на форму композера. Для ChatGPT это
             выглядит как настоящее перетаскивание мышью — запускается
             штатный upload-pipeline, файл реально уходит в backend.
          2. Fallback — скрепка + set_input_files. Превью появляется быстро,
             но ChatGPT иногда не принимает контент файла, отправленного
             таким путём (видно превью, но модель «не видит» вложение).
             Поэтому это запасной вариант.
          3. Жёсткая проверка: имена файлов видны в композере и spinner
             загрузки пропал — иначе RuntimeError.
        """
        if not file_paths:
            raise ValueError("_attach_files: file_paths пустой")

        page = await self._page_ready()
        await self._dismiss_no_auth_modal(page)

        for fp in file_paths:
            if not fp.exists():
                raise FileNotFoundError(f"upload: файл не найден {fp}")

        names = ", ".join(p.name for p in file_paths)
        logger.info(
            "ChatGPT: attach_logic={} — аплоад {} файлов [{}]",
            CHATGPT_ATTACH_LOGIC_ID,
            len(file_paths),
            names,
        )

        await self._clear_composer_attachments()
        before = await self._count_attachment_previews()

        # --- Шаг 1: drag-and-drop эмуляция (главный путь). ---
        drag_drop_ok = False
        try:
            logger.info("ChatGPT: drag-drop batch — [{}]", names)
            await self._drag_drop_files(file_paths)
            await self._wait_attachments_ready(file_paths, timeout=120)
            if await self._files_visible_in_composer(file_paths):
                drag_drop_ok = True
                logger.info("ChatGPT: drag-drop batch — все файлы видны [{}]", names)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "ChatGPT: drag-drop batch не удался ({}) — paperclip batch",
                e,
            )
            await self._clear_composer_attachments()

        # --- Шаг 2: paperclip + set_input_files (fallback). ---
        if not drag_drop_ok:
            try:
                logger.info("ChatGPT: paperclip batch fallback — [{}]", names)
                await self._attach_batch_via_paperclip(file_paths)
                drag_drop_ok = True
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ChatGPT: paperclip batch fallback упал ({}) — по одному",
                    e,
                )
                await self._clear_composer_attachments()
                accumulated: list[Path] = []
                for i, fp in enumerate(file_paths, start=1):
                    accumulated.append(fp)
                    logger.info(
                        "ChatGPT: paperclip incremental {}/{} — {}",
                        i,
                        len(file_paths),
                        fp.name,
                    )
                    page = await self._page_ready()
                    input_sel = await self._materialize_file_input(fresh=True)
                    await page.locator(input_sel).last.set_input_files([str(fp)])
                    await self._fire_file_input_events(input_sel)
                    await self._wait_attachments_ready(accumulated, timeout=120)
                drag_drop_ok = await self._files_visible_in_composer(file_paths)

        after = await self._count_attachment_previews()
        attached = after - before
        if attached < len(file_paths):
            logger.warning(
                "ChatGPT: аплоад {}/{} превью — пробую batch fallback",
                attached,
                len(file_paths),
            )
            await self._attach_files_batch(file_paths)
            after = await self._count_attachment_previews()
            attached = after - before

        if attached < len(file_paths) or not await self._files_visible_in_composer(
            file_paths
        ):
            await self._dump_composer_html()
            raise RuntimeError(
                f"ChatGPT: прикреплено {attached}/{len(file_paths)} файлов "
                f"[{names}] — отправку отменяю"
            )
        logger.info(
            "ChatGPT: все {} файлов в композере (превью +{})",
            len(file_paths),
            attached,
        )

    async def _attach_one_file(self, file_path: Path) -> None:
        """Один файл: drag-drop, при неудаче — set_input_files."""
        page = await self._page_ready()
        before = await self._count_attachment_previews()

        drag_drop_ok = False
        try:
            await self._drag_drop_files([file_path])
            preview_sel = await _first_matching(
                page, FILE_PREVIEW_SELECTORS, timeout=25
            )
            if preview_sel and file_path.name in await self._composer_attachment_text():
                logger.info(
                    "ChatGPT: drag-drop превью для {} ({})",
                    file_path.name,
                    preview_sel,
                )
                await self._wait_upload_done(timeout=120)
                drag_drop_ok = True
            else:
                logger.warning(
                    "ChatGPT: drag-drop без превью/имени для {} — fallback",
                    file_path.name,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "ChatGPT: drag-drop для {} упал ({}), fallback",
                file_path.name,
                e,
            )

        after = await self._count_attachment_previews()
        if drag_drop_ok and after > before:
            return

        logger.info("ChatGPT: set_input_files для {}", file_path.name)
        input_sel = await self._materialize_file_input(fresh=True)

        if not input_sel:
            await self._dump_composer_html()
            raise RuntimeError(
                f"ChatGPT: не удалось прикрепить {file_path.name} "
                "(нет input[type=file])"
            )

        await page.locator(input_sel).last.set_input_files([str(file_path)])
        await self._fire_file_input_events(input_sel)
        preview_sel = await _first_matching(
            page, FILE_PREVIEW_SELECTORS, timeout=60
        )
        if not preview_sel or file_path.name not in await self._composer_attachment_text():
            await self._dump_composer_html()
            raise RuntimeError(
                f"ChatGPT: {file_path.name} — превью/имя не появилось после set_input_files"
            )
        await self._wait_upload_done(timeout=120)

    async def _attach_files_batch(self, file_paths: list[Path]) -> None:
        """Batch drag-drop / set_input_files — запасной путь для нескольких файлов."""
        page = await self._page_ready()
        names = ", ".join(p.name for p in file_paths)

        drag_drop_ok = False
        try:
            await self._drag_drop_files(file_paths)
            preview_sel = await _first_matching(
                page, FILE_PREVIEW_SELECTORS, timeout=20
            )
            if preview_sel and await self._files_visible_in_composer(file_paths):
                logger.info(
                    "ChatGPT: batch drag-drop превью ({})", preview_sel
                )
                await self._wait_upload_done(timeout=120)
                drag_drop_ok = True
        except Exception as e:  # noqa: BLE001
            logger.warning("ChatGPT: batch drag-drop упал ({}), fallback", e)

        if drag_drop_ok:
            return

        logger.info("ChatGPT: batch fallback set_input_files [{}]", names)
        input_sel = await self._materialize_file_input(fresh=True)

        if not input_sel:
            raise RuntimeError(
                "ChatGPT: batch fallback — нет input[type=file]"
            )

        await page.locator(input_sel).last.set_input_files(
            [str(p) for p in file_paths]
        )
        await self._fire_file_input_events(input_sel)
        preview_sel = await _first_matching(
            page, FILE_PREVIEW_SELECTORS, timeout=60
        )
        if not preview_sel or not await self._files_visible_in_composer(file_paths):
            raise RuntimeError(
                f"ChatGPT: batch set_input_files — нет превью/имён [{names}]"
            )
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
        """Ждёт пока в композере исчезнут спиннеры загрузки вложений."""
        page = await self._page_ready()
        spinner_sels = [
            "form [data-testid*='attachment'] [role='progressbar']",
            "form [data-testid*='file-preview'] [role='progressbar']",
            "form [data-testid*='attachment'] .animate-spin",
            "form [data-testid*='file-preview'] .animate-spin",
            "form [data-testid*='uploading']",
            "form [role='progressbar']",
            "form [aria-busy='true']",
            "form [aria-label*='ploading']",
            "form [data-testid*='loading']",
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
        project_id: int | None = None,
    ) -> str:
        """Прикрепляет файлы, вводит сопр. текст в композер и отправляет."""
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        abort_if_cancelled(project_id)
        await self._attach_files(file_paths)
        abort_if_cancelled(project_id)

        attached = await self._count_attachment_previews()
        if attached < len(file_paths):
            raise RuntimeError(
                f"ChatGPT: перед отправкой только {attached}/{len(file_paths)} "
                "превью вложений"
            )
        logger.info(
            "ChatGPT: {} вложений в композере, ввожу текст ({} симв.)",
            attached,
            len(prompt),
        )

        if (prompt or "").strip():
            await self._send_prompt(prompt, clear_first=False)
        else:
            await self._click_send()

        await self._wait_until_done(timeout=timeout, project_id=project_id)

        # Ждём стабилизации текста (как в обычном ask), но не строго — Code
        # Interpreter иногда генерирует файл и сразу отдаёт короткий текст.
        page = await self._page_ready()
        last_text = ""
        stable_for = 0.0
        deadline = asyncio.get_event_loop().time() + 120.0
        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
            await sleep_cancellable(1.0, project_id)
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
