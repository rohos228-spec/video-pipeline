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
from typing import Any

from loguru import logger
from playwright.async_api import Download, Page

from app.bots.browser import BrowserSession

CHATGPT_URL = "https://chatgpt.com/"

# Идентификатор логики attach/send — показывается в /api/studio-version.
# Если в UI v69, а backend_attach другой — Python не перезапущен после git pull.
CHATGPT_ATTACH_LOGIC_ID = "attach-guard-v84-download-fast"

_ANIM_PR_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_ANIM_PR_DOC_SUFFIXES = frozenset({".md", ".txt", ".pdf"})

# Ожидание загрузки вложений в композер (тяжёлые пачки PNG могут грузиться >60с).
ATTACH_UPLOAD_TIMEOUT_SEC = 180.0
ATTACH_UPLOAD_POLL_SEC = 5.0
# После «готово» ChatGPT иногда отклоняет xlsx через 1–5 с — ждём стабилизации.
ATTACH_SETTLE_SEC = 5.0
ATTACH_SETTLE_POLL_SEC = 0.5
ATTACH_GUARD_MAX_REPAIR = 3
ATTACH_WAIT_MAX_REPAIR = 3
# После исчезновения Stop — короткая пауза перед чтением DOM.
POST_STOP_SETTLE_SEC = 0.4
# Стабилизация текста ответа (только ask без файла на скачивание).
REPLY_STABILIZE_SEC = 6.0
# ask_with_files + expect_file_download: ждём карточку файла, не 6с текста.
REPLY_STABILIZE_FILE_SEC = 1.5
DOWNLOAD_FILE_CARD_WAIT_SEC = 20.0
FILE_CARD_POST_READY_SEC = 0.3
# Фазы поиска кнопки/карточки скачивания (не весь download_timeout).
DOWNLOAD_PHASE_TIMEOUT_SEC = 15.0
DOWNLOAD_PHASE_RETRY_SEC = 20.0
TEXT_REPLY_DOWNLOAD_SUFFIXES = frozenset({".txt", ".md"})

# Фразы ошибок загрузки в композере (EN/RU).
ATTACHMENT_FAILURE_PHRASES: tuple[str, ...] = (
    "upload failed",
    "failed to upload",
    "couldn't upload",
    "could not upload",
    "unable to upload",
    "file too large",
    "exceeds the limit",
    "unsupported file",
    "invalid file",
    "error uploading",
    "не удалось загрузить",
    "ошибка загрузки",
    "не удалось прикрепить",
    "файл слишком больш",
    "неподдерживаем",
)

# ChatGPT при повторном drop одного имени добавляет суффикс: frame_001.png → frame_001(3).png
_ATTACHMENT_DEDUP_SUFFIX = re.compile(r"\(\d+\)")


def composer_text_already_present(expected: str, draft: str) -> bool:
    """Текст промта уже в композере (не дублирован)."""
    exp = (expected or "").strip()
    dr = (draft or "").strip()
    if not exp:
        return True
    if not dr:
        return False
    if dr == exp:
        return True
    if len(dr) >= len(exp) * 1.6:
        return False
    head = exp[: min(50, len(exp))]
    if dr.startswith(head) and len(dr) <= len(exp) + 40:
        return True
    return False


def composer_text_is_duplicated(expected: str, draft: str) -> bool:
    """Промт вставлен дважды (append вместо replace)."""
    exp = (expected or "").strip()
    dr = (draft or "").strip()
    if not exp or not dr:
        return False
    if len(dr) < len(exp) * 1.6:
        return False
    head = exp[: min(80, len(exp))]
    return dr.count(head) >= 2 or dr.startswith(head + head[: min(40, len(head))])


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


def find_attachment_failure_phrases(composer_text: str) -> list[str]:
    """Подстроки из ATTACHMENT_FAILURE_PHRASES, найденные в тексте композера."""
    hay = (composer_text or "").lower()
    if not hay:
        return []
    return [p for p in ATTACHMENT_FAILURE_PHRASES if p in hay]


def attachment_health_is_ok(health: dict) -> bool:
    return (
        not health.get("errors")
        and not health.get("missing")
        and int(health.get("count") or 0) >= int(health.get("expected") or 0)
        and int(health.get("loading") or 0) == 0
    )


def format_attachment_health_error(health: dict) -> str:
    parts: list[str] = []
    count = int(health.get("count") or 0)
    expected = int(health.get("expected") or 0)
    if count < expected:
        parts.append(f"count {count}/{expected}")
    loading = int(health.get("loading") or 0)
    if loading:
        parts.append(f"loading={loading}")
    missing = health.get("missing") or []
    if missing:
        parts.append(f"missing=[{', '.join(missing)}]")
    errors = health.get("errors") or []
    if errors:
        parts.append(f"errors=[{'; '.join(str(e) for e in errors[:3])}]")
    return ", ".join(parts) or "attachments not healthy"


def reply_text_usable_as_download(text: str, *, min_len: int = 10) -> bool:
    """GPT иногда кладёт voiceover в текст ответа, а не во вложение."""
    return len((text or "").strip()) >= min_len

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
DOWNLOAD_SPRITE_HASHES = ["d20dea", "1a3695"]
# Кнопка файла в prose: aria-label = имя файла (2026-Q2 UI).
ASSISTANT_FILE_BTN_SELECTORS = [
    f"{ASSISTANT_LAST_PREFIX} button.behavior-btn[aria-label$='.xlsx']",
    f"{ASSISTANT_LAST_PREFIX} button.behavior-btn[aria-label$='.xls']",
    f"{ASSISTANT_LAST_PREFIX} button.behavior-btn[aria-label$='.txt']",
    f"{ASSISTANT_LAST_PREFIX} button.behavior-btn[aria-label$='.csv']",
    f"{ASSISTANT_LAST_PREFIX} button[aria-label$='.xlsx']",
    f"{ASSISTANT_LAST_PREFIX} button[aria-label$='.txt']",
]
DOWNLOAD_LINK_SELECTORS = [
    *ASSISTANT_FILE_BTN_SELECTORS,
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
# UI 2026-Q2: клик по файлу открывает превью таблицы справа; скачивание —
# маленькая иконка ↓ «Скачать» в header панели (между «100%» и «X»), не тело таблицы.
FILE_PREVIEW_PANEL_SELECTORS = [
    "[data-testid='file-viewer']",
    "[data-testid*='spreadsheet-viewer']",
    "[data-testid*='artifact-viewer']",
    "[data-testid*='file-preview-panel']",
]
# Только header/toolbar панели — никогда не искать кнопку по всему окну превью.
FILE_PREVIEW_HEADER_SELECTORS = [
    "header",
    "[role='toolbar']",
    "div:has(button):has-text('%')",
]
FILE_PREVIEW_DOWNLOAD_SELECTORS = [
    "button[aria-label='Скачать']",
    "button[aria-label='Download']",
    "button[title='Скачать']",
    "button[title='Download']",
    "button[aria-label*='Скачать']",
    "button[aria-label*='Download']",
    "button[data-testid*='download']",
    *[
        f"button:has(use[href$='#{h}'])"
        for h in DOWNLOAD_SPRITE_HASHES
    ],
]
# Макс. размер кнопки ↓ в header (иконка ~32–40px; не кликать по телу таблицы).
FILE_PREVIEW_DOWNLOAD_BTN_MAX_PX = 56
FILE_PREVIEW_HEADER_STRIP_PX = 80
FILE_PREVIEW_OPEN_WAIT_SEC = 2.0
FILE_PREVIEW_DOWNLOAD_POLL_SEC = 25.0
PLAIN_FILE_DOWNLOAD_POLL_SEC = 8.0
# JS: глобальный поиск кнопки ↓ в toolbar превью xlsx (вне чата, правая верхняя зона).
_PREVIEW_DOWNLOAD_FIND_JS = """
([maxPx]) => {
    document.querySelectorAll('[data-vp-preview-download]').forEach((el) => {
        el.removeAttribute('data-vp-preview-download');
    });
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const inChat = (el) => !!el.closest(
        "[data-message-author-role='assistant'], "
        + "[data-message-author-role='user'], main form, nav"
    );
    const isDownload = (s) => {
        const t = (s || '').toLowerCase().trim();
        return t.includes('download') || t.includes('скач');
    };
    const isClose = (s) => {
        const t = (s || '').toLowerCase();
        return t.includes('close') || t.includes('закры');
    };

    const candidates = [];
    for (const btn of document.querySelectorAll('button, [role="button"]')) {
        if (inChat(btn)) continue;
        const br = btn.getBoundingClientRect();
        if (br.width < 14 || br.height < 14) continue;
        if (br.width > maxPx || br.height > maxPx) continue;
        if (br.left < vw * 0.40) continue;
        if (br.top > Math.max(240, vh * 0.42)) continue;
        const text = (btn.textContent || '').trim();
        if (text.includes('%')) continue;
        const al = btn.getAttribute('aria-label') || '';
        const title = btn.getAttribute('title') || '';
        if (isClose(al) || isClose(title)) continue;
        candidates.push({
            btn,
            right: br.right,
            left: br.left,
            top: br.top,
            w: Math.round(br.width),
            h: Math.round(br.height),
            al,
            title,
            hasSvg: !!btn.querySelector('svg'),
            isDl: isDownload(al) || isDownload(title),
        });
    }

    for (const c of candidates) {
        if (c.isDl) {
            c.btn.setAttribute('data-vp-preview-download', '1');
            return {
                found: true,
                via: 'global-label',
                al: c.al || c.title,
                w: c.w,
                h: c.h,
                n: candidates.length,
            };
        }
    }

    const icons = candidates
        .filter((c) => c.hasSvg)
        .sort((a, b) => b.right - a.right);
    if (icons.length >= 2) {
        const dl = icons[1];
        dl.btn.setAttribute('data-vp-preview-download', '1');
        return {
            found: true,
            via: 'global-penultimate',
            al: dl.al || dl.title || 'icon',
            w: dl.w,
            h: dl.h,
            n: candidates.length,
        };
    }

    return {
        found: false,
        n: candidates.length,
        sample: icons.slice(0, 4).map((c) => ({
            al: c.al,
            w: c.w,
            h: c.h,
            left: Math.round(c.left),
            top: Math.round(c.top),
        })),
    };
}
"""
# JS: кнопка ↓ для .txt — превью текста (без 100%, панель шире/ниже).
_PLAIN_FILE_DOWNLOAD_FIND_JS = """
([maxPx]) => {
    document.querySelectorAll('[data-vp-preview-download]').forEach((el) => {
        el.removeAttribute('data-vp-preview-download');
    });
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const inChat = (el) => !!el.closest(
        "[data-message-author-role='assistant'], "
        + "[data-message-author-role='user'], main form, nav"
    );
    const isDownload = (s) => {
        const t = (s || '').toLowerCase().trim();
        return t.includes('download') || t.includes('скач');
    };
    const isClose = (s) => {
        const t = (s || '').toLowerCase();
        return t.includes('close') || t.includes('закры');
    };

    const candidates = [];
    for (const btn of document.querySelectorAll('button, [role="button"]')) {
        if (inChat(btn)) continue;
        const br = btn.getBoundingClientRect();
        if (br.width < 14 || br.height < 14) continue;
        if (br.width > maxPx || br.height > maxPx) continue;
        if (br.left < vw * 0.28) continue;
        if (br.top > vh * 0.78) continue;
        const text = (btn.textContent || '').trim();
        if (text.includes('%')) continue;
        const al = btn.getAttribute('aria-label') || '';
        const title = btn.getAttribute('title') || '';
        if (isClose(al) || isClose(title)) continue;
        candidates.push({
            btn,
            right: br.right,
            al,
            title,
            hasSvg: !!btn.querySelector('svg'),
            isDl: isDownload(al) || isDownload(title),
        });
    }
    for (const c of candidates) {
        if (c.isDl) {
            c.btn.setAttribute('data-vp-preview-download', '1');
            return { found: true, via: 'plain-label', al: c.al || c.title, n: candidates.length };
        }
    }
    const icons = candidates.filter((c) => c.hasSvg).sort((a, b) => b.right - a.right);
    if (icons.length >= 1) {
        const dl = icons.length >= 2 ? icons[1] : icons[0];
        dl.btn.setAttribute('data-vp-preview-download', '1');
        return { found: true, via: 'plain-icon', al: dl.al || 'icon', n: candidates.length };
    }
    return { found: false, n: candidates.length };
}
"""
_PREVIEW_TOOLBAR_VISIBLE_JS = """
() => {
    const vw = window.innerWidth;
    const inChat = (el) => !!el.closest(
        "[data-message-author-role='assistant'], "
        + "[data-message-author-role='user'], main form"
    );
    for (const btn of document.querySelectorAll('button')) {
        if (inChat(btn)) continue;
        const br = btn.getBoundingClientRect();
        if ((btn.textContent || '').includes('%') && br.left > vw * 0.38) {
            return true;
        }
    }
    for (const el of document.querySelectorAll(
        "[role='grid'], table, canvas, [class*='sheet'], [class*='spreadsheet']"
    )) {
        if (inChat(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.width > vw * 0.28 && r.left > vw * 0.32 && r.height > 120) {
            return true;
        }
    }
    return false;
}
"""

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


_FILE_EXTENSIONS = (".xlsx", ".xls", ".txt", ".csv", ".json", ".md")
_SPREADSHEET_SUFFIXES = (".xlsx", ".xls", ".csv")
_PLAIN_FILE_SUFFIXES = (".txt", ".md", ".json")


def _min_download_bytes(target_path: Path) -> int:
    if target_path.suffix.lower() in _PLAIN_FILE_SUFFIXES:
        return 200
    return 512


def _uses_spreadsheet_preview(target_path: Path, label: str = "") -> bool:
    name = (label or target_path.name).lower()
    return any(name.endswith(ext) for ext in _SPREADSHEET_SUFFIXES)


def _backend_file_url_variants(url: str) -> list[str]:
    """ChatGPT /simple отдаёт превью (~300 байт); полный файл — /download или без /simple."""
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    add(url)
    if "/simple" in url:
        add(url.replace("/simple", "/download"))
        base, _, qs = url.partition("?")
        if base.endswith("/simple"):
            add(base[: -len("/simple")] + "/download" + (f"?{qs}" if qs else ""))
            add(base[: -len("/simple")] + (f"?{qs}" if qs else ""))
    if "/files/" in url and "/download" not in url:
        add(url.split("?")[0].rstrip("/") + "/download")
    return out


def _response_looks_like_file(resp: Any) -> bool:
    """HTTP-ответ похож на скачиваемый файл (ChatGPT иногда без download event)."""
    try:
        if not resp.ok:
            return False
        url = (resp.url or "").lower()
        ct = (resp.headers.get("content-type") or "").lower()
        if any(
            tok in ct
            for tok in (
                "spreadsheet",
                "excel",
                "octet-stream",
                "text/plain",
                "application/vnd",
            )
        ):
            return True
        if any(ext in url for ext in _FILE_EXTENSIONS):
            return True
        if "/files/" in url or "sandbox" in url or "file-" in url:
            return True
        # /simple — метаданные превью, не файл; обработаем через variants.
        if "/simple" in url:
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


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
        self,
        page: Page,
        *,
        timeout: float = ATTACH_UPLOAD_TIMEOUT_SEC,
        guard_file_paths: list[Path] | None = None,
    ) -> str:
        """Ждём активную кнопку Send (после ввода текста / загрузки файлов)."""
        deadline = asyncio.get_event_loop().time() + timeout
        started = asyncio.get_event_loop().time()
        last_log = 0.0
        repairs_during_wait = 0
        while asyncio.get_event_loop().time() < deadline:
            if guard_file_paths:
                health = await self._composer_attachment_health(guard_file_paths)
                if not attachment_health_is_ok(health):
                    detail = format_attachment_health_error(health)
                    if repairs_during_wait < ATTACH_WAIT_MAX_REPAIR:
                        repairs_during_wait += 1
                        logger.warning(
                            "ChatGPT: вложения деградировали во время ожидания Send "
                            "({}) — repair {}/{}",
                            detail,
                            repairs_during_wait,
                            ATTACH_WAIT_MAX_REPAIR,
                        )
                        try:
                            await self._guard_attachments_before_send(guard_file_paths)
                        except RuntimeError as repair_err:
                            logger.warning(
                                "ChatGPT: repair во время ожидания Send не помог: {}",
                                repair_err,
                            )
                        await asyncio.sleep(1.0)
                        continue
                    raise RuntimeError(
                        "ChatGPT: вложения деградировали пока ждали Send — "
                        f"{detail}"
                    )
            sel, ok = await self._is_send_button_enabled(page)
            if ok and sel:
                return sel
            now = asyncio.get_event_loop().time()
            if now - last_log >= ATTACH_UPLOAD_POLL_SEC:
                att_note = ""
                if guard_file_paths:
                    health = await self._composer_attachment_health(guard_file_paths)
                    att_note = (
                        f", attachments={health.get('count')}/"
                        f"{health.get('expected')}"
                    )
                logger.info(
                    "ChatGPT: жду активную кнопку Send… ({:.0f}/{:.0f}с{})",
                    now - started,
                    timeout,
                    att_note,
                )
                last_log = now
            await asyncio.sleep(ATTACH_UPLOAD_POLL_SEC)
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
        guard_file_paths: list[Path] | None = None,
    ) -> None:
        """Клик по активной Send + проверка (как в рабочем TG/xlsx-flow)."""
        await self._dismiss_no_auth_modal(page)
        user_before = await self._count_user_messages()
        send_sel = await self._wait_send_button_enabled(
            page,
            timeout=send_timeout,
            guard_file_paths=guard_file_paths,
        )
        if guard_file_paths:
            await self._guard_attachments_before_send(guard_file_paths)
            await self._wait_attachments_stable(
                guard_file_paths, settle_sec=min(ATTACH_SETTLE_SEC, 3.0)
            )
        btn = page.locator(send_sel).first
        logger.info(
            "ChatGPT: Send активна ({}) — финальная проверка вложений ok, клик",
            send_sel,
        )
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

    async def _set_composer_text_replace(
        self, page: Page, text: str, input_sel: str
    ) -> None:
        """Заменить текст композера (не append) — insertText дублирует при ретраях."""
        stripped = (text or "").strip()
        if not stripped:
            return
        locator = page.locator(input_sel).first
        await locator.click()
        await locator.focus()
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
        await asyncio.sleep(0.6)

    async def _fill_composer_text(self, page: Page, text: str, input_sel: str) -> None:
        """Ввод текста в ProseMirror / textarea (replace, без дублирования)."""
        stripped = (text or "").strip()
        if not stripped:
            return
        draft = await self._composer_draft_text()
        if composer_text_already_present(stripped, draft):
            logger.info(
                "ChatGPT: текст уже в композере ({} симв.), пропускаю ввод",
                len(draft),
            )
            return
        if composer_text_is_duplicated(stripped, draft):
            logger.warning(
                "ChatGPT: дубликат текста в композере ({} vs {} симв.) — заменяю",
                len(draft),
                len(stripped),
            )
        await self._set_composer_text_replace(page, stripped, input_sel)

    async def _click_send(
        self, *, guard_file_paths: list[Path] | None = None
    ) -> None:
        """Нажать Send без ввода текста в композер (только вложения)."""
        if guard_file_paths:
            await self._guard_attachments_before_send(guard_file_paths)
        page = await self._page_ready()
        await self._dispatch_composer_send(
            page,
            had_draft=False,
            send_timeout=ATTACH_UPLOAD_TIMEOUT_SEC,
            guard_file_paths=guard_file_paths,
        )

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

    async def _send_prompt(
        self,
        text: str,
        *,
        clear_first: bool = True,
        guard_file_paths: list[Path] | None = None,
        composer_text: str | None = None,
    ) -> None:
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
        effective = (
            (composer_text if composer_text is not None else text) or ""
        ).strip()
        if effective:
            await self._fill_composer_text(page, effective, input_sel)
        stripped = effective
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
        max_send_attempts = (
            ATTACH_GUARD_MAX_REPAIR if guard_file_paths else 1
        )
        last_err: RuntimeError | None = None
        for send_attempt in range(1, max_send_attempts + 1):
            try:
                if guard_file_paths:
                    await self._guard_attachments_before_send(guard_file_paths)
                await self._dispatch_composer_send(
                    page,
                    had_draft=bool(stripped),
                    send_timeout=ATTACH_UPLOAD_TIMEOUT_SEC
                    if not clear_first
                    else 45.0,
                    guard_file_paths=guard_file_paths if not clear_first else None,
                )
                return
            except RuntimeError as e:
                last_err = e
                page = await self._page_ready()
                for sel in STOP_BUTTON_SELECTORS:
                    try:
                        if await page.locator(sel).count() > 0:
                            logger.info(
                                "ChatGPT: генерация уже идёт — "
                                "не повторяю send/re-attach"
                            )
                            return
                    except Exception:  # noqa: BLE001
                        continue
                msg = str(e).lower()
                if (
                    not guard_file_paths
                    or send_attempt >= max_send_attempts
                    or ("вложен" not in msg and "attachment" not in msg)
                ):
                    raise
                logger.warning(
                    "ChatGPT: send attempt {}/{} failed ({}), re-attach",
                    send_attempt,
                    max_send_attempts,
                    e,
                )
                await self._attach_files(guard_file_paths)
                if stripped:
                    input_sel = await _first_matching(page, INPUT_SELECTORS, timeout=30)
                    if input_sel:
                        draft = await self._composer_draft_text()
                        if not composer_text_already_present(stripped, draft):
                            await self._fill_composer_text(
                                page, stripped, input_sel
                            )
        if last_err is not None:
            raise last_err

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
                await sleep_cancellable(POST_STOP_SETTLE_SEC, project_id)
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

    async def _composer_attachment_dom_errors(self) -> list[str]:
        """Ошибки загрузки в DOM композера (alert, data-testid=error, красные плитки)."""
        page = await self._page_ready()
        raw = await page.evaluate(
            """() => {
                const form = document.querySelector('main form')
                    || document.querySelector('form[data-type="unified-composer"]')
                    || document.querySelector('form');
                if (!form) return ['composer form not found'];
                const errors = [];
                const push = (t) => {
                    const s = (t || '').trim();
                    if (s && s.length < 300 && !errors.includes(s)) errors.push(s);
                };
                for (const sel of [
                    "[role='alert']",
                    "[data-testid*='error']",
                    "[data-testid*='upload-failed']",
                    "[data-testid*='upload_failed']",
                ]) {
                    for (const el of form.querySelectorAll(sel)) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) push(el.innerText || el.textContent);
                    }
                }
                for (const tile of form.querySelectorAll(
                    "[data-testid*='attachment'], [data-testid*='file-preview'], "
                    + "[class*='attachment'], [class*='Attachment']"
                )) {
                    const r = tile.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    const t = (tile.innerText || tile.textContent || '').trim();
                    const cls = String(tile.className || '');
                    const style = window.getComputedStyle(tile);
                    const failText = /fail|error|couldn|unable|invalid|too large|limit|не удал|ошибк/i.test(t);
                    const redish = /red|destructive|error/i.test(cls)
                        || /rgb\\(239|rgb\\(220|rgb\\(248, 113|rgb\\(185, 28/.test(style.color)
                        || /rgb\\(239|rgb\\(220|rgb\\(248, 113|rgb\\(185, 28/.test(style.borderColor);
                    if (failText || (redish && t.length > 0 && !/remove|удал/i.test(t))) {
                        push(t);
                    }
                }
                return errors;
            }"""
        )
        return [str(x) for x in (raw or []) if str(x).strip()]

    async def _composer_attachment_health(
        self, file_paths: list[Path]
    ) -> dict[str, int | str | bool | list[str]]:
        """Полная проверка вложений: count, имена, спиннеры, ошибки UI."""
        expected = len(file_paths)
        state = await self._attachments_upload_state(expected)
        text = await self._composer_attachment_text()
        labels = await self._composer_attachment_labels()
        haystack = f"{text}\n{labels}".strip()
        missing = [
            fp.name
            for fp in file_paths
            if not attachment_name_visible_in_text(fp.name, haystack)
        ]
        dom_errors = await self._composer_attachment_dom_errors()
        phrase_errors = find_attachment_failure_phrases(haystack)
        errors = list(dict.fromkeys([*dom_errors, *phrase_errors]))
        count = int(state.get("count") or 0)
        loading = int(state.get("loading") or 0)
        health: dict[str, int | str | bool | list[str]] = {
            "ok": False,
            "count": count,
            "expected": expected,
            "loading": loading,
            "missing": missing,
            "errors": errors,
        }
        health["ok"] = attachment_health_is_ok(health)
        return health

    async def _wait_attachments_stable(
        self,
        file_paths: list[Path],
        *,
        settle_sec: float = ATTACH_SETTLE_SEC,
    ) -> None:
        """Ждём, что вложения не «отвалятся» после первичного ok (xlsx часто позже)."""
        deadline = asyncio.get_event_loop().time() + settle_sec
        started = asyncio.get_event_loop().time()
        last_log = 0.0
        while asyncio.get_event_loop().time() < deadline:
            health = await self._composer_attachment_health(file_paths)
            if not attachment_health_is_ok(health):
                raise RuntimeError(
                    "ChatGPT: вложения нестабильны — "
                    f"{format_attachment_health_error(health)}"
                )
            now = asyncio.get_event_loop().time()
            if now - last_log >= ATTACH_UPLOAD_POLL_SEC:
                logger.info(
                    "ChatGPT: settle check {:.0f}/{:.0f}с — ok {}/{}",
                    now - started,
                    settle_sec,
                    health.get("count"),
                    health.get("expected"),
                )
                last_log = now
            await asyncio.sleep(ATTACH_SETTLE_POLL_SEC)

    async def _guard_attachments_before_send(
        self,
        file_paths: list[Path],
        *,
        max_repair_attempts: int = ATTACH_GUARD_MAX_REPAIR,
    ) -> None:
        """Перед Send: проверка + пересборка вложений при ошибке/пропаже файла."""
        if not file_paths:
            return
        names = ", ".join(p.name for p in file_paths)
        for attempt in range(1, max_repair_attempts + 1):
            health = await self._composer_attachment_health(file_paths)
            if attachment_health_is_ok(health):
                if attempt > 1:
                    logger.info(
                        "ChatGPT: attachment guard ok after repair {}/{} [{}]",
                        attempt,
                        max_repair_attempts,
                        names,
                    )
                return
            detail = format_attachment_health_error(health)
            logger.warning(
                "ChatGPT: attachment guard fail {}/{} [{}] — {}",
                attempt,
                max_repair_attempts,
                names,
                detail,
            )
            if attempt >= max_repair_attempts:
                await self._dump_composer_html()
                raise RuntimeError(
                    f"ChatGPT: вложения не готовы к отправке — {detail} [{names}]"
                )
            await self._clear_composer_attachments()
            await asyncio.sleep(0.6)
            await self._attach_files(file_paths, _skip_settle=True)
            await self._wait_attachments_stable(file_paths)

    async def _wait_attachments_ready(
        self,
        file_paths: list[Path],
        *,
        timeout: float = ATTACH_UPLOAD_TIMEOUT_SEC,
    ) -> None:
        """Ждём имена файлов в композере и исчезновение спиннеров на вложениях."""
        expected = len(file_paths)
        deadline = asyncio.get_event_loop().time() + timeout
        started = asyncio.get_event_loop().time()
        last_log_at = 0.0
        while asyncio.get_event_loop().time() < deadline:
            names_ok = await self._files_visible_in_composer(file_paths)
            state = await self._attachments_upload_state(expected)
            count = int(state.get("count") or 0)
            loading = int(state.get("loading") or 0)
            reason = state.get("reason", "?")
            count_ok = count >= expected
            now = asyncio.get_event_loop().time()
            if now - last_log_at >= ATTACH_UPLOAD_POLL_SEC:
                logger.info(
                    "ChatGPT: upload check {:.0f}/{:.0f}с — names_ok={} "
                    "count={}/{} loading={} ({})",
                    now - started,
                    timeout,
                    names_ok,
                    count,
                    expected,
                    loading,
                    reason,
                )
                last_log_at = now
            if names_ok and count_ok and loading == 0:
                await asyncio.sleep(0.8)
                return
            await asyncio.sleep(ATTACH_UPLOAD_POLL_SEC)
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
        await self._wait_attachments_ready(file_paths)

    async def _attach_one_via_paperclip(self, file_path: Path) -> None:
        """Прикрепить один файл через скрепку + set_input_files."""
        await self._attach_batch_via_paperclip([file_path])

    async def _attach_files(
        self, file_paths: list[Path], *, _skip_settle: bool = False
    ) -> None:
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
            await self._wait_attachments_ready(file_paths)
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
                    await self._wait_attachments_ready(accumulated)
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
        if not _skip_settle:
            await self._wait_attachments_stable(file_paths)

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
                await self._wait_upload_done()
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
        await self._wait_upload_done()

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
                await self._wait_upload_done()
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
        await self._wait_upload_done()

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

    async def _wait_upload_done(
        self, *, timeout: float = ATTACH_UPLOAD_TIMEOUT_SEC
    ) -> None:
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
        started = asyncio.get_event_loop().time()
        last_log_at = 0.0
        while asyncio.get_event_loop().time() < deadline:
            total = 0
            for sel in spinner_sels:
                try:
                    total += await page.locator(sel).count()
                except Exception:  # noqa: BLE001
                    continue
            now = asyncio.get_event_loop().time()
            if now - last_log_at >= ATTACH_UPLOAD_POLL_SEC:
                logger.info(
                    "ChatGPT: upload-spinner check {:.0f}/{:.0f}с — count={}",
                    now - started,
                    timeout,
                    total,
                )
                last_log_at = now
            if total == 0:
                await asyncio.sleep(0.8)
                return
            await asyncio.sleep(ATTACH_UPLOAD_POLL_SEC)
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
        timeout: float = 1800,
    ) -> str:
        """В текущем чате прикрепляет файл, шлёт промт, возвращает текст ответа.

        Файл, который GPT может вернуть в ответ, скачивается отдельным методом
        `download_attachment_from_last_reply` после успешного ask_with_file.
        """
        return await self.ask_with_files(prompt, [file_path], timeout=timeout)

    async def _wait_reply_after_generation(
        self,
        *,
        timeout: float,
        project_id: int | None,
        expect_file_download: bool,
    ) -> str:
        """После Stop: fast-path для xlsx/файла или стабилизация текста."""
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        page = await self._page_ready()
        if expect_file_download:
            card_sel = await _first_matching(
                page,
                FILE_CARD_SELECTORS,
                timeout=min(DOWNLOAD_FILE_CARD_WAIT_SEC, timeout * 0.15),
            )
            if card_sel:
                await sleep_cancellable(FILE_CARD_POST_READY_SEC, project_id)
                reply = await self._read_last_reply()
                logger.info(
                    "ChatGPT (file reply): карточка файла готова (fast-path), len={}",
                    len(reply),
                )
                return reply
            logger.info(
                "ChatGPT (file reply): карточка не появилась — короткая стабилизация текста"
            )
            stabilize_target = REPLY_STABILIZE_FILE_SEC
            min_stable_len = 0
        else:
            stabilize_target = REPLY_STABILIZE_SEC
            min_stable_len = 50

        last_text = ""
        stable_for = 0.0
        deadline = asyncio.get_event_loop().time() + min(120.0, timeout * 0.25)
        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
            await sleep_cancellable(0.5, project_id)
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
            if expect_file_download:
                card_sel = await _first_matching(page, FILE_CARD_SELECTORS, timeout=0.05)
                if card_sel:
                    await sleep_cancellable(FILE_CARD_POST_READY_SEC, project_id)
                    reply = await self._read_last_reply()
                    logger.info(
                        "ChatGPT (file reply): карточка появилась при стабилизации, len={}",
                        len(reply),
                    )
                    return reply
            if text == last_text and len(text) >= min_stable_len:
                stable_for += 0.5
                if stable_for >= stabilize_target:
                    break
            else:
                stable_for = 0.0
                last_text = text
        reply = await self._read_last_reply()
        logger.info("ChatGPT (file reply) len={}", len(reply))
        return reply

    async def ask_with_files(
        self,
        prompt: str,
        file_paths: list[Path],
        *,
        timeout: float = 1800,
        project_id: int | None = None,
        expect_file_download: bool = False,
    ) -> str:
        """Прикрепляет файлы, вводит сопр. текст в композер и отправляет."""
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        abort_if_cancelled(project_id)
        stripped = (prompt or "").strip()

        # Сначала текст, потом файлы: ввод после attach часто сбрасывает xlsx/txt.
        if stripped:
            page = await self._page_ready()
            await self._dismiss_no_auth_modal(page)
            input_sel = await _first_matching(page, INPUT_SELECTORS, timeout=30)
            if not input_sel:
                raise RuntimeError("ChatGPT: не найден input для промта")
            await self._fill_composer_text(page, stripped, input_sel)
            logger.info(
                "ChatGPT: текст в композере ({} симв.), прикрепляю {} файлов",
                len(stripped),
                len(file_paths),
            )
        else:
            logger.info("ChatGPT: без текста — прикрепляю {} файлов", len(file_paths))

        await self._attach_files(file_paths)
        abort_if_cancelled(project_id)

        if stripped:
            await self._send_prompt(
                "",
                clear_first=False,
                guard_file_paths=file_paths,
                composer_text=stripped,
            )
        else:
            await self._click_send(guard_file_paths=file_paths)

        await self._wait_until_done(timeout=timeout, project_id=project_id)
        return await self._wait_reply_after_generation(
            timeout=timeout,
            project_id=project_id,
            expect_file_download=expect_file_download,
        )

    async def ask_anim_pr_initial(
        self,
        prompt: str,
        master_prompt_file: Path,
        *,
        timeout: float = 300,
        project_id: int | None = None,
    ) -> str:
        """Шаг anim_pr фаза 1: только сопр. текст + мастер-промт файлом (без картинок).

        После ответа GPT очищает черновые вложения в композере — дальше пачки PNG.
        """
        suf = master_prompt_file.suffix.lower()
        if suf in _ANIM_PR_IMAGE_SUFFIXES:
            raise ValueError(
                f"anim_pr initial: ожидался .md/.txt, не картинка: {master_prompt_file.name}"
            )
        if suf not in _ANIM_PR_DOC_SUFFIXES:
            logger.warning(
                "anim_pr initial: нестандартное расширение {} — всё равно шлём только этот файл",
                suf,
            )
        logger.info(
            "anim_pr ФАЗА 1: текст ({} симв.) + файл {} — БЕЗ изображений",
            len((prompt or "").strip()),
            master_prompt_file.name,
        )
        reply = await self.ask_with_files(
            prompt,
            [master_prompt_file],
            timeout=timeout,
            project_id=project_id,
        )
        removed = await self._clear_composer_attachments()
        logger.info(
            "anim_pr ФАЗА 1 готова: ответ {} симв., очищено черновых вложений: {}",
            len(reply or ""),
            removed,
        )
        return reply

    async def ask_anim_pr_batch(
        self,
        prompt: str,
        image_paths: list[Path],
        *,
        timeout: float = 600,
        project_id: int | None = None,
    ) -> str:
        """Шаг anim_pr фаза 2: только PNG/JPEG + текст (ID + закадровый), без мастер-файла."""
        if not image_paths:
            raise ValueError("anim_pr batch: нет изображений")
        for fp in image_paths:
            if fp.suffix.lower() not in _ANIM_PR_IMAGE_SUFFIXES:
                raise ValueError(
                    f"anim_pr batch: только картинки, не {fp.name}"
                )
        logger.info(
            "anim_pr ФАЗА 2: {} влож. + текст ({} симв.) — мастер-файл не прикрепляем",
            len(image_paths),
            len((prompt or "").strip()),
        )
        return await self.ask_with_files(
            prompt,
            image_paths,
            timeout=timeout,
            project_id=project_id,
        )

    async def _fetch_url_to_path(
        self,
        page: Page,
        url: str,
        target_path: Path,
        *,
        min_size: int,
        label: str = "",
    ) -> bool:
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            resp = await page.request.get(url, timeout=30_000)
            if not resp.ok:
                return False
            body = await resp.body()
            if len(body) < min_size:
                return False
            target_path.write_bytes(body)
            logger.info(
                "ChatGPT: fetch {} → {} ({} байт, url={})",
                label or "api",
                target_path.name,
                len(body),
                url[:120],
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("ChatGPT: fetch {} failed: {}", url[:80], exc)
            return False

    async def _save_backend_file_variants(
        self,
        page: Page,
        api_url: str,
        target_path: Path,
        *,
        min_size: int,
        label: str = "",
    ) -> bool:
        for variant in _backend_file_url_variants(api_url):
            if await self._fetch_url_to_path(
                page,
                variant,
                target_path,
                min_size=min_size,
                label=f"{label or 'api'}:{variant[-40:]}",
            ):
                return True
        return False

    async def _click_and_save_file(
        self,
        page: Page,
        locator: Any,
        target_path: Path,
        *,
        label: str = "",
    ) -> bool:
        """Клик по кнопке файла: expect_download, иначе перехват HTTP-ответа."""
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        min_size = _min_download_bytes(target_path)

        try:
            async with page.expect_download(timeout=20_000) as dl_info:
                await locator.click(timeout=5_000)
            dl: Download = await dl_info.value
            size = await self._save_download_to_path(dl, target_path)
            logger.info(
                "ChatGPT: download event ({}) → {} ({} байт)",
                label or "file-btn",
                target_path.name,
                size,
            )
            return size >= min_size
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "ChatGPT: download event не сработал ({}): {}",
                label or "file-btn",
                exc,
            )

        try:
            async with page.expect_response(
                _response_looks_like_file, timeout=20_000
            ) as resp_info:
                await locator.click(timeout=5_000)
            resp = await resp_info.value
            body = await resp.body()
            if len(body) < min_size:
                logger.warning(
                    "ChatGPT: HTTP-ответ слишком мал ({} байт) url={}",
                    len(body),
                    resp.url,
                )
                if "/backend-api/files/" in resp.url:
                    if await self._save_backend_file_variants(
                        page,
                        resp.url,
                        target_path,
                        min_size=min_size,
                        label=label or "api-variants",
                    ):
                        return True
                return False
            target_path.write_bytes(body)
            logger.info(
                "ChatGPT: файл из HTTP ({}) → {} ({} байт, url={})",
                label or "file-btn",
                target_path.name,
                len(body),
                resp.url[:120],
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "ChatGPT: HTTP fallback не сработал ({}): {}",
                label or "file-btn",
                exc,
            )
        return False

    async def _all_browser_pages(self) -> list[Page]:
        """Все открытые вкладки CDP (превью xlsx иногда не на вкладке чата)."""
        pages: list[Page] = []
        seen: set[int] = set()
        try:
            chat = await self._page_ready()
            pages.append(chat)
            seen.add(id(chat))
        except Exception:  # noqa: BLE001
            pass
        ctx = self.session.context
        if ctx is not None:
            for pg in ctx.pages:
                try:
                    if pg.is_closed() or id(pg) in seen:
                        continue
                    pages.append(pg)
                    seen.add(id(pg))
                except Exception:  # noqa: BLE001
                    continue
        return pages

    async def _preview_toolbar_visible(self, page: Page | None = None) -> bool:
        """Превью xlsx справа уже открыто (100% / grid на правой половине)."""
        scan = [page] if page is not None else await self._all_browser_pages()
        for pg in scan:
            if pg is None:
                continue
            try:
                if await pg.evaluate(_PREVIEW_TOOLBAR_VISIBLE_JS):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def _locate_global_preview_download_button(
        self,
        page: Page | None = None,
    ) -> tuple[Any | None, Page | None]:
        """Кнопка ↓ «Скачать» в toolbar превью — поиск по всем вкладкам."""
        scan = [page] if page is not None else await self._all_browser_pages()
        for pg in scan:
            if pg is None:
                continue
            try:
                meta = await pg.evaluate(
                    _PREVIEW_DOWNLOAD_FIND_JS,
                    [FILE_PREVIEW_DOWNLOAD_BTN_MAX_PX],
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "ChatGPT: preview download scan failed on {}: {}",
                    getattr(pg, "url", "?")[:80],
                    exc,
                )
                continue
            if not meta or not meta.get("found"):
                n = (meta or {}).get("n", 0)
                if n:
                    logger.debug(
                        "ChatGPT: кандидаты toolbar на {}: {} sample={}",
                        getattr(pg, "url", "?")[:60],
                        n,
                        (meta or {}).get("sample"),
                    )
                continue
            logger.info(
                "ChatGPT: кнопка ↓ toolbar превью на {} ({}, {}x{}px, al={}, n={})",
                getattr(pg, "url", "?")[:60],
                meta.get("via"),
                meta.get("w"),
                meta.get("h"),
                (meta.get("al") or "")[:40],
                meta.get("n"),
            )
            loc = pg.locator("[data-vp-preview-download='1']").last
            if await loc.count() > 0:
                return loc, pg
        return None, None

    async def _last_assistant_file_button(self, page: Page) -> Any | None:
        """Кнопка файла в последнем ответе ассистента (behavior-btn .xlsx)."""
        selectors = [
            *ASSISTANT_FILE_BTN_SELECTORS,
            f"{ASSISTANT_LAST_PREFIX} button.behavior-btn",
        ]
        for sel in selectors:
            loc = page.locator(sel)
            count = await loc.count()
            if count == 0:
                continue
            for idx in range(count - 1, -1, -1):
                btn = loc.nth(idx)
                aria = (await btn.get_attribute("aria-label")) or ""
                if sel.endswith("behavior-btn") and aria:
                    if not any(
                        aria.lower().endswith(ext) for ext in _FILE_EXTENSIONS
                    ):
                        continue
                return btn
        return None

    async def _download_from_side_preview_panel(
        self,
        page: Page,
        target_path: Path,
    ) -> bool:
        """Скачать через кнопку ↓ в toolbar превью (все вкладки Chrome)."""
        download_btn, dl_page = await self._locate_global_preview_download_button()
        if download_btn is None or dl_page is None:
            return False
        try:
            await dl_page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        try:
            await download_btn.hover(timeout=2_000)
        except Exception:  # noqa: BLE001
            pass
        return await self._click_and_save_file(
            dl_page,
            download_btn,
            target_path,
            label="preview-toolbar-download",
        )

    async def _locate_plain_file_download_button(
        self,
        page: Page | None = None,
    ) -> tuple[Any | None, Page | None]:
        """Кнопка ↓ для .txt превью (без 100% zoom)."""
        scan = [page] if page is not None else await self._all_browser_pages()
        for pg in scan:
            if pg is None:
                continue
            try:
                meta = await pg.evaluate(
                    _PLAIN_FILE_DOWNLOAD_FIND_JS,
                    [FILE_PREVIEW_DOWNLOAD_BTN_MAX_PX],
                )
            except Exception:  # noqa: BLE001
                continue
            if not meta or not meta.get("found"):
                continue
            logger.info(
                "ChatGPT: кнопка ↓ txt-превью на {} ({}, al={})",
                getattr(pg, "url", "?")[:60],
                meta.get("via"),
                (meta.get("al") or "")[:40],
            )
            loc = pg.locator("[data-vp-preview-download='1']").last
            if await loc.count() > 0:
                return loc, pg
        return None, None

    async def _download_plain_file_preview(
        self,
        page: Page,
        target_path: Path,
    ) -> bool:
        """Скачать .txt/.md: toolbar превью или API /download."""
        for finder in (
            self._locate_global_preview_download_button,
            self._locate_plain_file_download_button,
        ):
            download_btn, dl_page = await finder(page)
            if download_btn is None or dl_page is None:
                continue
            try:
                await dl_page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
            try:
                await download_btn.hover(timeout=1_500)
            except Exception:  # noqa: BLE001
                pass
            if await self._click_and_save_file(
                dl_page,
                download_btn,
                target_path,
                label="plain-preview-download",
            ):
                return True
        return False

    async def _click_plain_file_then_download(
        self,
        page: Page,
        locator: Any,
        target_path: Path,
        *,
        label: str = "",
    ) -> bool:
        """.txt/.md: без ожидания xlsx-превью — API /download или кнопка ↓ сразу."""
        tag = label or "file-btn"
        min_size = _min_download_bytes(target_path)
        try:
            await locator.scroll_into_view_if_needed(timeout=4_000)
        except Exception:  # noqa: BLE001
            pass

        captured: list[str] = []

        def _on_response(resp: Any) -> None:
            try:
                u = resp.url or ""
                if "/backend-api/files/" in u:
                    captured.append(u)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_response)
        try:
            await locator.click(timeout=5_000)
            logger.info("ChatGPT: клик по .txt в чате ({})", tag)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ChatGPT: клик .txt не удался ({}): {}", tag, exc)
            page.remove_listener("response", _on_response)
            return False
        await asyncio.sleep(0.6)
        page.remove_listener("response", _on_response)

        for api_url in captured:
            if await self._save_backend_file_variants(
                page,
                api_url,
                target_path,
                min_size=min_size,
                label=f"{tag}-api",
            ):
                return True

        deadline = asyncio.get_event_loop().time() + PLAIN_FILE_DOWNLOAD_POLL_SEC
        while asyncio.get_event_loop().time() < deadline:
            if await self._download_plain_file_preview(page, target_path):
                return True
            await asyncio.sleep(0.35)

        logger.info(
            "ChatGPT: .txt не скачан за {}с ({})",
            PLAIN_FILE_DOWNLOAD_POLL_SEC,
            tag,
        )
        try:
            await page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        return False

    async def _click_spreadsheet_file_then_download(
        self,
        page: Page,
        locator: Any,
        target_path: Path,
        *,
        label: str = "",
    ) -> bool:
        """.xlsx: превью таблицы справа → кнопка ↓ в toolbar."""
        tag = label or "file-btn"
        preview_open = await self._preview_toolbar_visible(page)
        if not preview_open:
            try:
                await locator.scroll_into_view_if_needed(timeout=4_000)
                await locator.click(timeout=5_000)
                logger.info(
                    "ChatGPT: клик по файлу в чате → открыть превью ({})",
                    tag,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "ChatGPT: клик по файлу для превью не удался ({}): {}",
                    tag,
                    exc,
                )
                return False
            await asyncio.sleep(FILE_PREVIEW_OPEN_WAIT_SEC)

        deadline = asyncio.get_event_loop().time() + FILE_PREVIEW_DOWNLOAD_POLL_SEC
        while asyncio.get_event_loop().time() < deadline:
            if await self._preview_toolbar_visible(page):
                if await self._download_from_side_preview_panel(page, target_path):
                    return True
            elif await self._download_from_side_preview_panel(page, target_path):
                return True
            await asyncio.sleep(0.5)

        logger.info(
            "ChatGPT: toolbar xlsx / кнопка ↓ не найдены за {}с ({})",
            FILE_PREVIEW_DOWNLOAD_POLL_SEC,
            tag,
        )
        if await self._click_and_save_file(
            page, locator, target_path, label=f"{tag}-legacy"
        ):
            return True
        try:
            await page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        return False

    async def _click_file_then_download(
        self,
        page: Page,
        locator: Any,
        target_path: Path,
        *,
        label: str = "",
    ) -> bool:
        """Скачивание по типу файла: .txt быстро, .xlsx через превью таблицы."""
        tag = label or target_path.name
        if _uses_spreadsheet_preview(target_path, tag):
            return await self._click_spreadsheet_file_then_download(
                page, locator, target_path, label=tag
            )
        return await self._click_plain_file_then_download(
            page, locator, target_path, label=tag
        )

    async def _try_download_aria_file_buttons(
        self,
        page: Page,
        target_path: Path,
        *,
        timeout: float = 60,
    ) -> bool:
        """Один клик по файлу в чате → превью справа → ↓ (без цикла повторных кликов)."""
        _ = timeout
        btn = await self._last_assistant_file_button(page)
        if btn is None:
            return False
        aria = (await btn.get_attribute("aria-label")) or "behavior-btn"
        return await self._click_file_then_download(
            page,
            btn,
            target_path,
            label=aria,
        )

    async def _try_download_via_file_card(
        self,
        page: Page,
        target_path: Path,
        *,
        timeout: float = 60,
    ) -> bool:
        """Скачать файл кликом по карточке (behavior-btn) или панели превью справа."""
        card_sel = await _first_matching(
            page, FILE_CARD_SELECTORS, timeout=timeout,
        )
        if card_sel is None:
            logger.info(
                "ChatGPT: _try_download_via_file_card: карточка файла "
                "не найдена за {} сек",
                timeout,
            )
            return False

        loc = page.locator(card_sel)
        count = await loc.count()
        indices = list(range(count - 1, -1, -1)) if count else [0]
        for idx in indices:
            btn = loc.nth(idx) if count else loc.first
            aria = (await btn.get_attribute("aria-label")) or ""
            if aria and not any(aria.lower().endswith(ext) for ext in _FILE_EXTENSIONS):
                if "behavior-btn" in card_sel:
                    continue
            logger.info(
                "ChatGPT: пробую скачать файл кликом по карточке ({}, idx={}, aria={})",
                card_sel,
                idx,
                aria[:60],
            )
            if await self._click_file_then_download(
                page,
                btn,
                target_path,
                label=aria or card_sel,
            ):
                return True
        return False

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

    async def _save_download_to_path(self, download: Download, target_path: Path) -> int:
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(str(target_path))
        return target_path.stat().st_size if target_path.exists() else -1

    async def _try_download_via_selectors(
        self,
        page: Page,
        selectors: list[str],
        *,
        timeout: float = DOWNLOAD_PHASE_TIMEOUT_SEC,
    ) -> Download | None:
        """Перебирает селекторы и жмёт с expect_download (popover / assistant)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if await page.locator(sel).count() == 0:
                        continue
                    async with page.expect_download(timeout=8_000) as dl_info:
                        await loc.click(timeout=4_000)
                    dl: Download = await dl_info.value
                    logger.info(
                        "ChatGPT: download via selector {} filename={}",
                        sel,
                        dl.suggested_filename,
                    )
                    return dl
                except Exception:  # noqa: BLE001
                    continue
            await asyncio.sleep(0.4)
        return None

    async def _save_reply_text_as_file(
        self,
        target_path: Path,
        text: str,
        *,
        source: str,
    ) -> Path:
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        cleaned = text.strip()
        target_path.write_text(cleaned, encoding="utf-8")
        logger.info(
            "ChatGPT: saved {} as {} ({} chars, source={})",
            target_path.name,
            target_path,
            len(cleaned),
            source,
        )
        return target_path

    async def download_attachment_from_last_reply(
        self,
        target_path: Path,
        *,
        timeout: float = 1800,
        fallback_text: str | None = None,
        allow_reply_text_fallback: bool = False,
    ) -> Path:
        """Из последнего ответа ассистента скачивает файл в `target_path`.

        Фазы (каждая ограничена ~60 с, не всем `timeout`):
          1. Клик по карточке файла (behavior-btn).
          2. Селекторы Download в ответе ассистента.
          3. Hover/click по карточке → повтор 1–2.
          4. Для .txt/.md — текст ответа GPT только если allow_reply_text_fallback.
        """
        page = await self._page_ready()
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        phase_timeout = min(
            DOWNLOAD_PHASE_TIMEOUT_SEC, max(60.0, timeout * 0.05)
        )

        aria_timeout = min(120.0, max(45.0, timeout * 0.06))
        is_plain = not _uses_spreadsheet_preview(target_path)
        if await self._try_download_aria_file_buttons(
            page, target_path, timeout=aria_timeout
        ):
            return target_path

        if is_plain:
            if await self._download_plain_file_preview(page, target_path):
                return target_path
        else:
            if await self._try_download_via_file_card(
                page, target_path, timeout=phase_timeout
            ):
                size = target_path.stat().st_size if target_path.exists() else -1
                logger.info(
                    "ChatGPT: файл скачан (behavior-btn / превью) как {} ({} байт)",
                    target_path,
                    size,
                )
                if size < 1024:
                    logger.warning(
                        "ChatGPT: размер подозрительно мал ({} байт).", size
                    )
                    await self._dump_last_assistant_html()
                return target_path

        download = await self._try_download_via_selectors(
            page, DOWNLOAD_LINK_SELECTORS, timeout=phase_timeout
        )
        if download is not None:
            size = await self._save_download_to_path(download, target_path)
            logger.info(
                "ChatGPT: файл скачан как {} (исходное имя {}, размер {} байт)",
                target_path,
                download.suggested_filename,
                size,
            )
            if size < 1024 and not is_plain:
                logger.warning(
                    "ChatGPT: размер скачанного файла подозрительно мал ({} байт).",
                    size,
                )
                await self._dump_last_assistant_html()
            return target_path

        if is_plain:
            await self._dump_last_assistant_html()
            raise RuntimeError(
                "ChatGPT: ссылка на скачивание не найдена в ответе (.txt). "
                "Полный outerHTML последнего ответа залогирован."
            )

        await self._hover_file_cards()
        retry_timeout = min(DOWNLOAD_PHASE_RETRY_SEC, max(12.0, timeout * 0.03))
        if await self._try_download_via_file_card(
            page, target_path, timeout=retry_timeout
        ):
            size = target_path.stat().st_size if target_path.exists() else -1
            logger.info(
                "ChatGPT: файл скачан после hover как {} ({} байт)",
                target_path,
                size,
            )
            return target_path
        if await self._download_from_side_preview_panel(page, target_path):
            return target_path
        download = await self._try_download_via_selectors(
            page, DOWNLOAD_LINK_SELECTORS, timeout=retry_timeout
        )
        if download is not None:
            size = await self._save_download_to_path(download, target_path)
            logger.info(
                "ChatGPT: файл скачан после hover (селектор) как {} ({} байт)",
                target_path,
                size,
            )
            return target_path

        if await self._try_download_aria_file_buttons(
            page, target_path, timeout=min(90.0, timeout * 0.05)
        ):
            return target_path

        suffix = target_path.suffix.lower()
        if allow_reply_text_fallback and suffix in TEXT_REPLY_DOWNLOAD_SUFFIXES:
            min_len = 500 if suffix == ".txt" else 10
            reply_text = (fallback_text or "").strip()
            if not reply_text_usable_as_download(reply_text, min_len=min_len):
                reply_text = await self._read_last_reply()
            if reply_text_usable_as_download(reply_text, min_len=min_len):
                return await self._save_reply_text_as_file(
                    target_path,
                    reply_text,
                    source="reply_text_fallback",
                )

        await self._dump_last_assistant_html()
        raise RuntimeError(
            "ChatGPT: ссылка на скачивание не найдена в ответе. "
            "Полный outerHTML последнего ответа залогирован — пришли строки "
            "из консоли с 'last assistant outerHTML' разработчику."
        )

