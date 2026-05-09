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
from typing import Any

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
    # Сначала пытаемся найти АКТИВНУЮ кнопку; только если не нашли —
    # берём любую (она может быть заблокирована пока не вставлен промт).
    "button:has-text('Генерировать'):not([disabled])",
    "button:has-text('Сгенерировать'):not([disabled])",
    "button:has-text('Создать'):not([disabled])",
    "button:has-text('Generate'):not([disabled])",
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


async def _dump_page(
    page: Any, label: str, *, max_html_chars: int = 400_000
) -> tuple[Path | None, Path | None]:
    """Сохраняет полный outerHTML и скриншот страницы в
    `<settings.data_dir>/outsee_dumps/<label>_<ts>.{html,png}`.
    Возвращает (html_path, png_path); элементы могут быть None если что-то
    не получилось.

    Используется для отладки селекторов на outsee.io: при ненайденной
    кнопке (aspect / relax / generate / др.) дампим страницу — потом
    оркестратор присылает файл в TG, и можно подобрать селектор.
    """
    from datetime import datetime as _dt

    dumps_dir = Path(settings.data_dir) / "outsee_dumps"
    dumps_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.utcnow().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)
    html_path = dumps_dir / f"{safe}_{ts}.html"
    png_path = dumps_dir / f"{safe}_{ts}.png"
    html_ok: Path | None = None
    png_ok: Path | None = None
    try:
        html = await page.content()
        if html and len(html) > max_html_chars:
            html = html[: max_html_chars // 2] + "\n<!-- ...truncated... -->\n" + html[-max_html_chars // 2 :]
        html_path.write_text(html or "", encoding="utf-8")
        html_ok = html_path
    except Exception as e:  # noqa: BLE001
        logger.warning("outsee dump html failed for {}: {}", label, e)
    try:
        await page.screenshot(path=str(png_path), full_page=True, timeout=10_000)
        png_ok = png_path
    except Exception as e:  # noqa: BLE001
        logger.warning("outsee dump png failed for {}: {}", label, e)
    if html_ok or png_ok:
        logger.info(
            "outsee dump '{}': html={} png={}",
            label,
            html_ok and html_ok.name,
            png_ok and png_ok.name,
        )
    return html_ok, png_ok


def _aspect_selectors(ratio: str) -> list[str]:
    """Набор CSS-селекторов для кнопки выбора aspect ratio.

    Работает для любого формата `W:H` — 1:1, 16:9, 9:16, 4:3, 3:4, 2:3,
    3:2, 21:9. На outsee.io это могут быть button / div role=radio / div
    role=button / label с текстом внутри. Чем точнее селектор — тем
    меньше шансов попасть в текст «9:16» внутри hint-подписи где-то
    ещё на странице.
    """
    return [
        # Самые точные — кнопки/радио с текстом ровно ratio.
        f"button:has-text('{ratio}')",
        f"[role='radio']:has-text('{ratio}')",
        f"[role='button']:has-text('{ratio}')",
        f"label:has-text('{ratio}')",
        f"[data-value='{ratio}']",
        f"[aria-label='{ratio}']",
        f"input[value='{ratio}']",
        # Менее точно — любой родитель с дочерним :text-is.
        f"*:has(> :text-is('{ratio}'))",
    ]


# Селекторы dropdown-кнопки «Соотношение …» (открывает выбор aspect).
# В outsee.io это <button>, у которого внутри лежит <span>«Соотношение»</span>
# и <span>текущее_значение</span>.
ASPECT_DROPDOWN_OPENER_SELECTORS: list[str] = [
    "button:has(span:text-is('Соотношение'))",
    "button:has-text('Соотношение')",
    "[role='button']:has-text('Соотношение')",
]


def _aspect_option_selectors(ratio: str) -> list[str]:
    """Селекторы пункта со значением aspect ratio в открывшемся
    dropdown-списке. Текст пункта может быть ровно W:H."""
    return [
        f"[role='option']:text-is('{ratio}')",
        f"[role='menuitem']:text-is('{ratio}')",
        f"[role='radio']:text-is('{ratio}')",
        f"button:text-is('{ratio}')",
        f"li:text-is('{ratio}')",
        f"div[role]:text-is('{ratio}')",
        f"span:text-is('{ratio}')",
        # Любой кликабельный родитель, у которого ровно ratio в дочернем span.
        f"button:has(> span:text-is('{ratio}'))",
        f"li:has(span:text-is('{ratio}'))",
    ]


async def _is_aspect_selected(page: Any, sel: str) -> bool | None:
    """Проверка, выбран ли вариант aspect-ratio после клика. None — не
    смогли определить (тогда не уверены)."""
    try:
        loc = page.locator(sel).first
        for attr, want in (
            ("aria-checked", "true"),
            ("aria-pressed", "true"),
            ("data-state", "checked"),
            ("data-state", "active"),
            ("data-state", "selected"),
        ):
            try:
                v = await loc.get_attribute(attr, timeout=200)
                if v is not None:
                    if str(v).lower() == want:
                        return True
            except Exception:  # noqa: BLE001
                continue
        # Класс с маркером "selected" / "active" / "checked".
        try:
            cls = await loc.get_attribute("class", timeout=200) or ""
            cls_low = cls.lower()
            for marker in ("selected", "active", "checked", "is-active"):
                if marker in cls_low:
                    return True
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        return None
    return None


async def _select_aspect_ratio(
    page: Any, ratio: str, *, where: str = "image",
    dumps: list[Path] | None = None,
) -> bool:
    """Выбирает aspect ratio в outsee.io. Поддерживает 2 типа UI:

    1) **Dropdown-кнопка «Соотношение …»** (новый UI 2026): в правой
       панели лежит <button> с двумя <span>: «Соотношение» + текущее
       значение. Клик открывает попап со списком вариантов; кликаем
       нужный.
    2) **Прямая кнопка/радио с текстом ratio** (старый UI / fallback).

    Если кнопка не найдена — дампит страницу в outsee_dumps/ и (если
    передан dumps-список) добавляет туда пути файлов; вызывающий код
    может потом отправить их в TG."""
    # 1) Сначала пробуем NEW UI: dropdown «Соотношение N:M».
    opener_sel = await _first_visible(
        page, ASPECT_DROPDOWN_OPENER_SELECTORS, timeout_ms=2_000
    )
    if opener_sel:
        try:
            opener = page.locator(opener_sel).first
            # Если в кнопке уже стоит нужное значение — ничего не делаем.
            try:
                cur_text = (await opener.inner_text(timeout=1_000)) or ""
            except Exception:  # noqa: BLE001
                cur_text = ""
            if ratio in cur_text:
                logger.info(
                    "outsee.{}: aspect {} уже выбран в dropdown ({})",
                    where, ratio, cur_text.strip().replace("\n", " ")[:80],
                )
                return True
            try:
                await opener.scroll_into_view_if_needed(timeout=1_500)
            except Exception:  # noqa: BLE001
                pass
            await opener.click(timeout=3_000)
            logger.info(
                "outsee.{}: aspect dropdown открыт (был '{}', хочу '{}')",
                where, cur_text.strip().replace("\n", " ")[:60], ratio,
            )
            await asyncio.sleep(0.3)
            opt_sel = await _first_visible(
                page, _aspect_option_selectors(ratio), timeout_ms=4_000
            )
            if opt_sel:
                try:
                    await page.locator(opt_sel).first.click(timeout=3_000)
                    logger.info(
                        "outsee.{}: aspect {} — выбран в dropdown ({})",
                        where, ratio, opt_sel,
                    )
                    await asyncio.sleep(0.3)
                    return True
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "outsee.{}: aspect {} клик в dropdown упал: {} ({})",
                        where, ratio, e, opt_sel,
                    )
            else:
                logger.warning(
                    "outsee.{}: aspect dropdown открыт, но опция '{}' "
                    "не найдена",
                    where, ratio,
                )
                # Пытаемся закрыть dropdown (Escape), чтобы не мешал.
                try:
                    await page.keyboard.press("Escape")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.{}: dropdown «Соотношение» поломался: {}",
                where, e,
            )

    # 2) Fallback: ищем прямую кнопку/радио с текстом ratio (старый UI).
    sel = await _first_visible(page, _aspect_selectors(ratio), timeout_ms=4_000)
    if not sel:
        logger.warning(
            "outsee.{}: aspect {} — ни dropdown «Соотношение», ни "
            "прямая кнопка не найдены",
            where, ratio,
        )
        h, p = await _dump_page(page, f"aspect_{ratio.replace(':', 'x')}_notfound")
        if dumps is not None:
            for x in (h, p):
                if x:
                    dumps.append(x)
        return False
    try:
        loc = page.locator(sel).first
        try:
            await loc.scroll_into_view_if_needed(timeout=2_000)
        except Exception:  # noqa: BLE001
            pass
        await loc.click(timeout=3_000)
        logger.info(
            "outsee.{}: aspect {} — клик ({})", where, ratio, sel
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "outsee.{}: aspect {} клик упал: {} (sel={})",
            where, ratio, e, sel,
        )
        return False

    await asyncio.sleep(0.3)
    ok = await _is_aspect_selected(page, sel)
    if ok is True:
        logger.info(
            "outsee.{}: aspect {} подтверждён выбран (sel={})",
            where, ratio, sel,
        )
        return True
    try:
        sel2 = await _first_visible(
            page, _aspect_selectors(ratio), timeout_ms=2_000
        )
        if sel2 and sel2 != sel:
            await page.locator(sel2).first.click(timeout=2_000)
            logger.info(
                "outsee.{}: aspect {} — повторный клик по другому селектору ({})",
                where, ratio, sel2,
            )
    except Exception:  # noqa: BLE001
        pass
    return True


def _resolution_selectors(resolution: str) -> list[str]:
    """Селекторы для кнопки 2K / 4K (картинка) или 720p / 1080p (видео)."""
    return [
        f"button:has-text('{resolution}')",
        f"[data-value='{resolution}']",
        f"[aria-label='{resolution}']",
        f"*:has(> :text-is('{resolution}'))",
    ]


# Кнопка/тогл «Relax» (для всех картиночных моделей и для veo-3-1-fast).
# В outsee.io это просто кнопка/тогл с текстом «Relax».
RELAX_SELECTORS: list[str] = [
    "button:has-text('Relax')",
    "[role='switch']:has-text('Relax')",
    "label:has-text('Relax')",
    "[aria-label='Relax']",
    "[data-value='relax']",
    "*:has(> :text-is('Relax'))",
]

# Селекторы тогла «Безлимит» — на outsee.io это и есть Relax (юзер
# подтвердил, что «Безлимит» = Relax-режим = включается при relax=True).
# bg-primary = тогл ВКЛ (Relax включён). bg-gray-*/без bg-primary = ВЫКЛ.
LIMIT_TOGGLE_SELECTORS: list[str] = [
    "button:has(span:text-is('Безлимит'))",
    "button:has-text('Безлимит')",
    "[role='switch']:has-text('Безлимит')",
]


async def _read_limit_toggle_on(page: Any, sel: str) -> bool | None:
    """True если тогл «Безлимит» включён, False если выключен,
    None если не смогли определить."""
    try:
        loc = page.locator(sel).first
        try:
            cls = await loc.locator("div.rounded-full").first.get_attribute(
                "class", timeout=500
            ) or ""
            cls_low = cls.lower()
            if "bg-primary" in cls_low:
                return True
            if "bg-gray" in cls_low or "bg-zinc" in cls_low:
                return False
        except Exception:  # noqa: BLE001
            pass
        # Запасной способ — позиция «шарика» (left-[18px] = ON).
        try:
            ball_cls = await loc.locator("div.absolute").first.get_attribute(
                "class", timeout=300
            ) or ""
            ball_low = ball_cls.lower()
            if "left-[18px]" in ball_low:
                return True
            if "left-[2px]" in ball_low:
                return False
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        return None
    return None


async def _toggle_relax(
    page: Any, *, want_on: bool, where: str = "image",
    dumps: list[Path] | None = None,
) -> None:
    """Best-effort: ставит тогл Relax в нужное состояние.

    На outsee.io 2026 года тогл называется «Безлимит» — и это И ЕСТЬ
    Relax-режим (юзер подтвердил):
      Relax=ON  ⇔ Безлимит=ON
      Relax=OFF ⇔ Безлимит=OFF

    Если тогл не нашёлся — пробуем старые «Relax»-селекторы. Если совсем
    нет — тихо выходим (модель его не поддерживает). Если want_on=True и
    кнопку не нашли — дампим страницу для отладки.
    """
    # 1) Сначала пробуем NEW UI: «Безлимит».
    limit_sel = await _first_visible(
        page, LIMIT_TOGGLE_SELECTORS, timeout_ms=1_500
    )
    if limit_sel:
        try:
            current_on = await _read_limit_toggle_on(page, limit_sel)
            # Семантика: relax want_on == True ⇔ Безлимит должно быть ON.
            desired_limit_on = want_on
            if current_on is desired_limit_on:
                logger.info(
                    "outsee.{}: Relax {} — Безлимит уже {} (тогл не трогаем)",
                    where, "ON" if want_on else "OFF",
                    "OFF" if desired_limit_on is False else "ON",
                )
                return
            if current_on is None:
                logger.info(
                    "outsee.{}: Relax {} — состояние «Безлимит» неизвестно, "
                    "кликаю один раз",
                    where, "ON" if want_on else "OFF",
                )
            await page.locator(limit_sel).first.click(timeout=2_000)
            logger.info(
                "outsee.{}: тогл «Безлимит» переключён → хочу Relax={}",
                where, "ON" if want_on else "OFF",
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.{}: тогл «Безлимит» поломался: {}", where, e,
            )

    # 2) Fallback: старые «Relax»-селекторы.
    sel = await _first_visible(page, RELAX_SELECTORS, timeout_ms=2_000)
    if not sel:
        if want_on:
            logger.warning(
                "outsee.{}: Relax=on запрошен, но ни тогл «Безлимит», ни "
                "кнопка «Relax» не найдены",
                where,
            )
            h, p = await _dump_page(page, "relax_notfound")
            if dumps is not None:
                for x in (h, p):
                    if x:
                        dumps.append(x)
        return
    try:
        loc = page.locator(sel).first
        state: str | None = None
        for attr in ("aria-checked", "aria-pressed", "data-state"):
            try:
                v = await loc.get_attribute(attr, timeout=500)
                if v is not None:
                    state = str(v).lower()
                    break
            except Exception:  # noqa: BLE001
                continue
        is_on: bool | None = None
        if state in ("true", "on", "checked"):
            is_on = True
        elif state in ("false", "off", "unchecked"):
            is_on = False
        if want_on and is_on is True:
            logger.info("outsee.{}: Relax уже включён, пропускаем клик", where)
            return
        if not want_on and is_on is False:
            logger.info("outsee.{}: Relax уже выключен, пропускаем клик", where)
            return
        if not want_on and is_on is None:
            logger.info(
                "outsee.{}: Relax=off запрошен, но состояние неизвестно — не трогаем",
                where,
            )
            return
        await loc.click(timeout=2_000)
        logger.info("outsee.{}: Relax {} (sel={})", where, "ON" if want_on else "OFF", sel)
    except Exception as e:  # noqa: BLE001
        logger.warning("outsee.{}: Relax toggle упал: {}", where, e)


def _image_page_url(model_slug: str | None) -> str:
    """Строит URL страницы outsee.io/image для нужной модели."""
    base = settings.outsee_image_url
    if not model_slug:
        return base
    # Если в settings.outsee_image_url уже есть `?model=...`, заменяем его
    # на выбранный slug. Иначе добавляем.
    if "?model=" in base:
        head = base.split("?model=")[0]
        return f"{head}?model={model_slug}"
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}model={model_slug}"


def _video_page_url(model_slug: str | None) -> str:
    base = settings.outsee_video_url
    if not model_slug:
        return base
    if "?model=" in base:
        head = base.split("?model=")[0]
        return f"{head}?model={model_slug}"
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}model={model_slug}"

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
    gen_id: str | None = None  # uuid v4, привязан к одной попытке (для трейсинга)
    # Пути к dump-файлам (html/png) если по ходу генерации не нашлась
    # какая-то кнопка (aspect/relax/etc.). Оркестратор отправляет их
    # в TG для отладки селекторов.
    dumps: list[Path] | None = None


class OutseeImageError(RuntimeError):
    """Ошибка с описательным контекстом — пайплайн использует это,
    чтобы запостить понятную ошибку в Telegram, а не системный traceback."""

    def __init__(
        self,
        reason: str,
        *,
        context: dict[str, Any] | None = None,
        dumps: list[Path] | None = None,
    ) -> None:
        self.reason = reason
        self.context = dict(context or {})
        # html/png дампы страницы для отладки селекторов outsee.io.
        self.dumps: list[Path] = list(dumps or [])
        super().__init__(self.format_text())

    def format_text(self) -> str:
        lines = [self.reason]
        for k, v in self.context.items():
            s = str(v)
            if len(s) > 200:
                s = s[:200] + "…"
            lines.append(f"  {k}: {s}")
        return "\n".join(lines)


class OutseeContentRejectedError(OutseeImageError):
    """Outsee показал плашку «Контент отклонён» (модерация запрещённых
    слов в промте). Отдельный класс, чтобы caller мог решить — ретраить
    с тем же промтом или просить GPT переписать его без триггеров.

    Сама `OutseeImageError` остаётся базовым классом, поэтому весь
    существующий error-handling в caller'ах продолжит работать без правок."""


# Минимум «настоящей» картинки из nano-banana — она всегда тяжелее 50 KB
# (обычно 300 KB – 2 MB). Логотипы/аватары/иконки outsee ≤ 10 KB.
_MIN_IMAGE_BYTES = 50_000

# Magic-байты файловых форматов, которые реально может вернуть nano-banana.
# Используется в `_validate_downloaded_image` для отсева HTML-страниц,
# error-pages и SVG-плейсхолдеров.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_RIFF_MAGIC = b"RIFF"
_WEBP_TAG = b"WEBP"


def _validate_downloaded_image(
    out_path: Path, *, gen_id: str, img_url: str
) -> None:
    """Проверяет, что скачанный файл — настоящая картинка от nano-banana,
    а не placeholder/skeleton/error-page.

    Бывает, что `_wait_image_url_strict` возвращает URL outsee-плейсхолдера
    (тёмный фон с тремя белыми квадратами — outsee показывает его, пока
    идёт генерация), и `_download_via_context` сохраняет этот мусор как
    «результат». Бот потом отправляет это в TG, и пользователь видит
    placeholder вместо реальной картинки.

    Проверки:
      1) размер файла >= `_MIN_IMAGE_BYTES` (50 KB) — placeholder/skeleton
         сжимается в единицы KB, реальная nano-banana картинка 300 KB+;
      2) magic-байты PNG/JPEG/WebP — отсекает HTML-страницы и SVG.

    На любую неудачу — удаляем «битый» файл (чтобы случайно не
    отправился в TG) и кидаем `OutseeImageError`. Retry-обёртка
    (`outsee_retry.generate_image_with_retries`) увидит ошибку и
    перезапустит генерацию с тем же или переписанным промтом.
    """
    try:
        size = out_path.stat().st_size
    except OSError as e:
        raise OutseeImageError(
            "outsee image: скачанный файл недоступен после download",
            context={
                "gen_id": gen_id,
                "img_url": img_url,
                "err": f"{type(e).__name__}: {e}",
            },
        ) from e

    if size < _MIN_IMAGE_BYTES:
        try:  # noqa: SIM105
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise OutseeImageError(
            "outsee image: скачанный файл слишком мал — похоже на "
            "placeholder/skeleton, а не реальную генерацию",
            context={
                "gen_id": gen_id,
                "img_url": img_url,
                "size_bytes": size,
                "min_bytes": _MIN_IMAGE_BYTES,
            },
        )

    try:
        with out_path.open("rb") as f:
            head = f.read(16)
    except OSError as e:
        raise OutseeImageError(
            "outsee image: не удалось прочитать заголовок скачанного файла",
            context={
                "gen_id": gen_id,
                "img_url": img_url,
                "err": f"{type(e).__name__}: {e}",
            },
        ) from e

    is_png = head.startswith(_PNG_MAGIC)
    is_jpeg = head.startswith(_JPEG_MAGIC)
    is_webp = head[:4] == _RIFF_MAGIC and head[8:12] == _WEBP_TAG
    if not (is_png or is_jpeg or is_webp):
        try:  # noqa: SIM105
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise OutseeImageError(
            "outsee image: скачанный файл не выглядит как PNG/JPEG/WebP "
            "(возможно, error-page или SVG-плейсхолдер)",
            context={
                "gen_id": gen_id,
                "img_url": img_url,
                "size_bytes": size,
                "head_hex": head.hex(),
            },
        )


# Пути, по которым точно не лежат результаты генерации.
_UI_ASSET_MARKERS = (
    "/_next/",
    "/static/",
    "/assets/",
    "/icons/",
    "/logo",
    "favicon",
    "sprite",
)

# Маркеры путей/имён, которые соответствуют ВЫБРАННОМУ ПОЛЬЗОВАТЕЛЕМ
# референсу (то, что мы только что загрузили в input[type=file]) либо
# его превью-копии. Эти URL outsee возвращает в DOM как «вот ваш инпут»
# через несколько секунд после клика Generate, и без этого фильтра мы
# их ошибочно принимаем за результат генерации (см. v=3-баг: бот сохранил
# /temp-images/3787/input_*.png вместо реального результата).
_INPUT_REF_MARKERS = (
    "/temp-images/",
    "/input_",
    "/uploads/",
    "/upload/",
)


def _url_is_fresh(
    url: str | None, net_events: list[tuple[float, str]] | None
) -> bool:
    """Returns True iff `url` actually came over the network in
    `net_events` (list of (offset_sec, url) tuples) AFTER the Generate
    click. Used by `_wait_image_url_strict` to filter out stale
    cached/history images that appear in the DOM without a real
    network load.

    Semantics:
      - `net_events is None` → caller didn't opt in; return True (legacy
        behaviour, keeps backwards compat for any other future caller).
      - `net_events == []` → caller opted in, but no candidate image
        responses have arrived yet — return False (we'll re-check in
        the next loop iteration).
      - `net_events` non-empty → return True iff `url` matches one of
        the events. Matching: exact string OR host+path equality
        (strips query strings/fragments).
    """
    if not url:
        return False
    if net_events is None:
        return True
    if not net_events:
        return False
    fresh_urls = {u for _, u in net_events}
    if url in fresh_urls:
        return True
    try:
        from urllib.parse import urlparse

        target = urlparse(url)
        for u in fresh_urls:
            try:
                p = urlparse(u)
            except Exception:  # noqa: BLE001
                continue
            if p.netloc == target.netloc and p.path == target.path:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _is_candidate_image_response(resp: Any) -> bool:
    """Подходит ли сетевой ответ под «вероятно, это результат nano-banana»:
    image/* (не svg/ico), не UI-ассет, тело ≥ 50 KB."""
    try:
        url = resp.url or ""
        ct = (resp.headers.get("content-type") or "").lower()
        if not ct.startswith("image/"):
            return False
        if ct in ("image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon"):
            return False
        low = url.lower()
        if any(marker in low for marker in _UI_ASSET_MARKERS):
            return False
        if any(marker in low for marker in _INPUT_REF_MARKERS):
            # Это URL ВХОДНОГО референса (тот файл, который мы только что
            # загрузили), а не результат генерации. Не считаем кандидатом.
            return False
        # Content-Length — дешёвый способ отсечь мелочь без .body()
        cl = resp.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) < _MIN_IMAGE_BYTES:
                    return False
            except ValueError:
                pass
        return True
    except Exception:  # noqa: BLE001
        return False


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
        timeout: float = 600,
        gen_id: str | None = None,
        model_slug: str | None = None,
        resolution: str | None = None,
        relax: bool = False,
        prompt_id_prefix: str | None = None,
        reference_image: Path | None = None,
    ) -> GenerationResult:
        """Генерирует картинку на outsee.io.

        Параметры:
          model_slug      — slug для URL (`?model=<slug>`). Если None —
                            используется settings.outsee_image_url как есть.
          aspect_ratio    — строка-ярлык кнопки («16:9», «9:16»…). Жмём
                            кнопку и проверяем состояние.
          resolution      — строка-ярлык («2K» / «4K»). Best-effort клик.
          relax           — если True и Relax-кнопка есть на странице — включаем.
          reference_image — если передан Path — загружаем картинку как
                            референс для генерации (через input[type=file]
                            на странице outsee.io). Используется в
                            hero-вариациях: первая вариация генерится без
                            референса, последующие — с первой как ref.
          prompt_id_prefix — строка вида `[ID: P12-F3-a7f2b01c]`. Будет
                             поставлена ПЕРВОЙ строкой промта, чтобы в
                             истории outsee однозначно отличать эту
                             генерацию от всех прошлых.
        """
        import time as _time
        import uuid as _uuid

        gen_id = gen_id or _uuid.uuid4().hex
        # Сюда копятся пути к dump-файлам страницы (html/png), создаваемые
        # хелперами при ненайденных кнопках. В конце этот список идёт в
        # GenerationResult.dumps — оркестратор отправит файлы в TG.
        dumps: list[Path] = []
        if prompt_id_prefix:
            prompt = f"{prompt_id_prefix}\n\n{prompt.lstrip()}"
            logger.info(
                "outsee.generate_image: prompt_id_prefix={}", prompt_id_prefix
            )

        page_url = _image_page_url(model_slug)
        logger.info(
            "outsee.generate_image: открываю страницу gen_id={} url={}",
            gen_id[:8], page_url,
        )
        page = await self.session.open_page(page_url, reuse=True)
        # ВАЖНО: всегда «прокидываем» goto, чтобы сбросить состояние от
        # предыдущей генерации (заполненный textarea, прикреплённый
        # референс, плашка «Контент отклонён»). Без этого ретрай после
        # ошибки на той же странице будет видеть остатки прошлой попытки
        # и сразу падать с тем же диагнозом.
        try:
            await page.goto(page_url, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.generate_image: page.goto({}) упал: {} — продолжаю "
                "без явного reload", page_url, e,
            )
        await page.wait_for_load_state("domcontentloaded")
        # Next.js-страница outsee гидратится дольше 3 сек — даём ей доразложиться.
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        logger.info("outsee.generate_image: страница готова, гидрация ok")

        # Снимок «до» — все большие картинки и URL-ы, которые уже на странице.
        # Свежим результатом считаем ТОЛЬКО то, чего тут не было.
        baseline_result_img = await self._result_img_src(page)
        baseline_big_imgs = set(await self._all_big_imgs(page))
        baseline_dom_srcs = set(await self._all_img_srcs(page))
        logger.info(
            "outsee.generate_image: baseline result_img={}, big_imgs={}, all_imgs={}",
            (baseline_result_img[:80] if baseline_result_img else None),
            len(baseline_big_imgs),
            len(baseline_dom_srcs),
        )

        # Сетевой listener — ловит ВСЕ image/* ответы с timestamp.
        # Используется только как мониторинг (для подсказки в ошибке),
        # НО не как fallback для «возьму последнюю» — это и есть тот баг,
        # из-за которого приходили чужие/старые картинки.
        click_ts = _time.monotonic()
        net_events: list[tuple[float, str]] = []  # (ts_offset_from_click, url)

        def _on_response(resp: Any) -> None:
            try:
                if not _is_candidate_image_response(resp):
                    return
                net_events.append((_time.monotonic() - click_ts, resp.url))
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_response)

        try:
            # 1) вбить промт
            input_sel = await _first_visible(
                page, PROMPT_INPUT_SELECTORS, timeout_ms=60_000
            )
            if not input_sel:
                h, p = await _dump_page(page, "prompt_input_notfound")
                for x in (h, p):
                    if x:
                        dumps.append(x)
                raise OutseeImageError(
                    "outsee image: не найден ввод промта",
                    context={"gen_id": gen_id},
                    dumps=dumps,
                )
            logger.info("outsee.generate_image: textarea найдена ({})", input_sel)
            try:
                await page.locator(input_sel).first.scroll_into_view_if_needed(
                    timeout=5_000
                )
            except Exception:  # noqa: BLE001
                pass
            await page.locator(input_sel).first.click()
            await page.locator(input_sel).first.fill(prompt)
            logger.info("outsee.generate_image: промт вставлен ({} симв)", len(prompt))

            # 2) выбрать aspect ratio (поддержка любого W:H, с верификацией)
            if aspect_ratio:
                await _select_aspect_ratio(
                    page, aspect_ratio, where="generate_image", dumps=dumps,
                )

            # 2.5) выбрать разрешение 2K / 4K (best-effort)
            if resolution:
                res_sel = await _first_visible(
                    page, _resolution_selectors(resolution), timeout_ms=3_000
                )
                if res_sel:
                    try:
                        await page.locator(res_sel).first.click()
                        logger.info(
                            "outsee.generate_image: {} выбран ({})",
                            resolution, res_sel,
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "resolution {} не кликнулось ({})", resolution, res_sel
                        )

            # 2.7) Relax (если попросили)
            await _toggle_relax(
                page, want_on=relax, where="generate_image", dumps=dumps,
            )

            # 2.9) Reference-картинка (для hero-вариаций 2..N).
            # На странице outsee.io image обычно есть СКРЫТЫй input[type=file]
            # для подгрузки референса. Обычный _first_visible его НЕ найдёт
            # (видимость=False), поэтому используем робастный хелпер
            # `_attach_ref_image_robust`: он первым делом пытается видимый
            # input, иначе берёт ЛЮБОЙ input[type=file] в DOM и бьёт
            # set_input_files в него (по Playwright он работает на скрытых тоже).
            if reference_image is not None:
                if not reference_image.exists():
                    logger.warning(
                        "outsee.generate_image: reference_image {} не найден на диске",
                        reference_image,
                    )
                else:
                    attached = await self._attach_ref_image_robust(
                        page, reference_image, where="generate_image",
                    )
                    if not attached:
                        h, p = await _dump_page(page, "ref_input_notfound")
                        for x in (h, p):
                            if x:
                                dumps.append(x)

            # 3) кнопка generate
            gen_sel = await _first_visible(page, GENERATE_BUTTON_SELECTORS, timeout_ms=10_000)
            if not gen_sel:
                h, p = await _dump_page(page, "generate_button_notfound")
                for x in (h, p):
                    if x:
                        dumps.append(x)
                raise OutseeImageError(
                    "outsee image: не найдена кнопка Generate",
                    context={"gen_id": gen_id},
                    dumps=dumps,
                )
            logger.info("outsee.generate_image: кнопка Generate найдена ({})", gen_sel)
            await self._wait_button_enabled(page, gen_sel, timeout_s=600)

            # Re-baseline ПОСЛЕ всех настроек (aspect dropdown, разрешение,
            # Relax, референс) — клики по dropdown вызывают ререндер
            # правой панели и могут «принести» в DOM другую картинку,
            # которую мы иначе ошибочно посчитаем «новым результатом».
            baseline_result_img = await self._result_img_src(page)
            baseline_big_imgs = set(await self._all_big_imgs(page))
            baseline_dom_srcs = set(await self._all_img_srcs(page))
            logger.info(
                "outsee.generate_image: re-baseline перед Generate "
                "result_img={}, big_imgs={}, all_imgs={}",
                (baseline_result_img[:80] if baseline_result_img else None),
                len(baseline_big_imgs),
                len(baseline_dom_srcs),
            )

            # Снимок текста плашки «Контент отклонён» ДО клика Generate.
            # На свежеоткрытой странице outsee часто рендерит остаток
            # rejection-плашки от предыдущего запроса (тот же браузерный
            # контекст / history). Передаём этот текст в детектор, чтобы
            # он не считал такую плашку «новой» ошибкой.
            pre_rejected_text = await self._content_rejected_text(page)
            if pre_rejected_text:
                logger.info(
                    "outsee.generate_image: pre-click rejected_text"
                    " обнаружена ({} симв) — игнорю, считаю её остатком"
                    " предыдущей попытки",
                    len(pre_rejected_text),
                )

            click_ts = _time.monotonic()
            net_events.clear()
            await page.locator(gen_sel).first.click()
            logger.info(
                "outsee.generate_image: Generate кликнут, жду картинку (gen_id={})",
                gen_id[:8],
            )

            # 4) строгое ожидание свежей картинки
            try:
                img_url = await self._wait_image_url_strict(
                    page,
                    timeout=timeout,
                    baseline_result_img=baseline_result_img,
                    baseline_big_imgs=baseline_big_imgs,
                    baseline_all_srcs=baseline_dom_srcs,
                    net_events=net_events,
                    gen_id=gen_id,
                    pre_rejected_text=pre_rejected_text,
                )
            except OutseeContentRejectedError as e:
                # Модерация — дамп НЕ снимаем (caller всё равно его не
                # покажет, см. требование «не слать дампы при ошибках
                # генерации»). Просто пробрасываем дальше — retry-обёртка
                # сама решит: ретраить тот же промт или просить GPT
                # переписать его без триггеров.
                e.dumps = list(dumps)
                raise
            except OutseeImageError as e:
                # При таймауте дампим страницу — пригодится для отладки
                # «почему Generate не запустил генерацию» и подбора селекторов.
                h, p = await _dump_page(page, "image_timeout")
                for x in (h, p):
                    if x:
                        dumps.append(x)
                e.dumps = list(dumps)
                raise
        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:  # noqa: BLE001
                pass

        # 5) скачиваем
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await _download_via_context(page, img_url, out_path)
        except Exception as e:  # noqa: BLE001
            raise OutseeImageError(
                "outsee image: скачивание результата упало",
                context={
                    "gen_id": gen_id,
                    "img_url": img_url,
                    "err": f"{type(e).__name__}: {e}",
                },
                dumps=dumps,
            ) from e

        # 5.1) валидация скачанного файла. Защита от placeholder/skeleton:
        # outsee.io иногда отдаёт «загрузочный» PNG (тёмный фон с тремя
        # белыми квадратами) как результат генерации, и без этой проверки
        # бот сохраняет его и шлёт в TG. На неудачу — кидаем OutseeImageError,
        # retry-обёртка повторит генерацию.
        try:
            _validate_downloaded_image(
                out_path, gen_id=gen_id, img_url=img_url
            )
        except OutseeImageError as e:
            e.dumps = list(dumps)
            raise

        logger.info("outsee image saved → {} (gen_id={})", out_path, gen_id[:8])
        return GenerationResult(
            file_path=out_path, raw_url=img_url, gen_id=gen_id,
            dumps=dumps or None,
        )

    async def regenerate_image(
        self,
        out_path: Path,
        *,
        timeout: float = 600,
        gen_id: str | None = None,
    ) -> GenerationResult:
        """Жмёт «Повторить» на существующем результате генерации — без ChatGPT,
        без перезаполнения промта. Сайт использует тот же промт и настройки."""
        import time as _time
        import uuid as _uuid

        gen_id = gen_id or _uuid.uuid4().hex
        page = await self.session.open_page(settings.outsee_image_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        baseline_result_img = await self._result_img_src(page)
        baseline_big_imgs = set(await self._all_big_imgs(page))
        baseline_dom_srcs = set(await self._all_img_srcs(page))

        # На regenerate тоже ведём список реальных сетевых image-ответов
        # ПОСЛЕ клика «Повторить» — это позволяет _wait_image_url_strict
        # отфильтровать старые картинки, которые могут объвиться в DOM
        # при ререндере карточки «Результат».
        click_ts = _time.monotonic()
        net_events: list[tuple[float, str]] = []

        def _on_response(resp: Any) -> None:
            try:
                if not _is_candidate_image_response(resp):
                    return
                net_events.append((_time.monotonic() - click_ts, resp.url))
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_response)

        try:
            retry_sel = await _first_visible(
                page,
                [
                    "button:has-text('Повторить')",
                    "button:has-text('Retry')",
                    "button:has-text('Regenerate')",
                ],
                timeout_ms=15_000,
            )
            if not retry_sel:
                raise OutseeImageError(
                    "outsee image: не найдена кнопка «Повторить» — на странице "
                    "нет предыдущего результата",
                    context={"gen_id": gen_id},
                )
            try:
                await page.locator(retry_sel).first.scroll_into_view_if_needed(
                    timeout=5_000
                )
            except Exception:  # noqa: BLE001
                pass
            # Снимок rejection-плашки ДО клика «Повторить» — см. коммент
            # в generate_image() про false-positive детект остатков.
            pre_rejected_text = await self._content_rejected_text(page)
            click_ts = _time.monotonic()
            net_events.clear()
            await page.locator(retry_sel).first.click()
            logger.info(
                "outsee.regenerate_image: «Повторить» кликнут, жду картинку (gen_id={})",
                gen_id[:8],
            )

            img_url = await self._wait_image_url_strict(
                page,
                timeout=timeout,
                baseline_result_img=baseline_result_img,
                baseline_big_imgs=baseline_big_imgs,
                baseline_all_srcs=baseline_dom_srcs,
                net_events=net_events,
                gen_id=gen_id,
                pre_rejected_text=pre_rejected_text,
            )
        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:  # noqa: BLE001
                pass

        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await _download_via_context(page, img_url, out_path)
        except Exception as e:  # noqa: BLE001
            raise OutseeImageError(
                "outsee image: скачивание результата (regenerate) упало",
                context={
                    "gen_id": gen_id,
                    "img_url": img_url,
                    "err": f"{type(e).__name__}: {e}",
                },
            ) from e

        # Валидация скачанного файла — см. комментарий в `generate_image`.
        _validate_downloaded_image(out_path, gen_id=gen_id, img_url=img_url)

        logger.info(
            "outsee image regenerated → {} (gen_id={})", out_path, gen_id[:8]
        )
        return GenerationResult(file_path=out_path, raw_url=img_url, gen_id=gen_id)

    async def _wait_button_enabled(
        self, page: Page, selector: str, *, timeout_s: float = 180
    ) -> None:
        """Ждёт пока кнопка станет активной (не disabled). На outsee Generate
        заблокирован, если идёт предыдущая генерация или пуст промт."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        last_log = 0.0
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() < deadline:
            try:
                loc = page.locator(selector).first
                disabled = await loc.get_attribute("disabled")
                aria = await loc.get_attribute("aria-disabled")
                if disabled is None and (aria or "").lower() != "true":
                    if (asyncio.get_event_loop().time() - start) > 1:
                        logger.info(
                            "outsee: Generate активен спустя {:.0f} сек",
                            asyncio.get_event_loop().time() - start,
                        )
                    return
            except Exception:  # noqa: BLE001
                pass
            now = asyncio.get_event_loop().time()
            if now - last_log > 15:
                last_log = now
                logger.info(
                    "outsee: жду пока Generate станет активной... ({:.0f} сек)",
                    now - start,
                )
            await asyncio.sleep(1.0)
        raise PWTimeoutError(
            "outsee image: кнопка Generate остаётся disabled — "
            "предыдущая генерация зависла?"
        )

    async def _result_img_src(self, page: Page) -> str | None:
        """Src большой картинки из блока «Результат генерации» (или None,
        если блока ещё нет / там плейсхолдер/спиннер)."""
        try:
            return await page.evaluate(
                """() => {
                    const imgs = Array.from(document.querySelectorAll('img'));
                    const keywords = ['Результат генерации', 'Результат', 'Result'];
                    for (const img of imgs) {
                        const r = img.getBoundingClientRect();
                        if (r.width < 200 || r.height < 200) continue;
                        let el = img;
                        for (let i = 0; i < 14 && el; i++) {
                            const t = el.textContent || '';
                            for (const kw of keywords) {
                                if (t.includes(kw)) return img.src || null;
                            }
                            el = el.parentElement;
                        }
                    }
                    return null;
                }"""
            )
        except Exception:  # noqa: BLE001
            return None

    async def _all_big_imgs(self, page: Page) -> list[str]:
        """Все изображения на странице с размером ≥200×200 (визуальный bbox)."""
        try:
            return await page.evaluate(
                """() => {
                    const out = [];
                    for (const img of document.querySelectorAll('img')) {
                        const r = img.getBoundingClientRect();
                        if (r.width >= 200 && r.height >= 200 && img.src) {
                            out.push(img.src);
                        }
                    }
                    return out;
                }"""
            )
        except Exception:  # noqa: BLE001
            return []

    async def _all_img_srcs(self, page: Page) -> list[str]:
        """Все непустые src на странице (для baseline-снимка)."""
        try:
            return await page.evaluate(
                """() => Array.from(document.querySelectorAll('img'))
                          .map(i => i.src).filter(Boolean)"""
            )
        except Exception:  # noqa: BLE001
            return []

    async def _completed_new_imgs(
        self, page: Page, baseline_srcs: set[str]
    ) -> list[str]:
        """Возвращает src всех `<img>` на странице, которые:
          - не были в baseline до старта генерации,
          - уже полностью загружены (img.complete && naturalWidth>0),
          - имеют natural-размер ≥200×200 (отсекает иконки/аватары).
        Список упорядочен в порядке появления в DOM (последний элемент —
        обычно самая «новая» карточка результата)."""
        baseline_list = list(baseline_srcs)
        try:
            res = await page.evaluate(
                """(baseline) => {
                    const skip = new Set(baseline);
                    const out = [];
                    for (const img of document.querySelectorAll('img')) {
                        if (!img.src) continue;
                        if (skip.has(img.src)) continue;
                        if (img.src.startsWith('data:')) continue;
                        if (img.src.includes('/placeholder.svg')) continue;
                        if (!img.complete) continue;
                        if (!img.naturalWidth || img.naturalWidth < 200) continue;
                        if (!img.naturalHeight || img.naturalHeight < 200) continue;
                        out.push(img.src);
                    }
                    return out;
                }""",
                baseline_list,
            )
            return list(res or [])
        except Exception:  # noqa: BLE001
            return []

    async def _wait_image_url_strict(
        self,
        page: Page,
        *,
        timeout: float,
        baseline_result_img: str | None,
        baseline_big_imgs: set[str],
        baseline_all_srcs: set[str],
        net_events: list[tuple[float, str]] | None = None,
        gen_id: str,
        pre_rejected_text: str | None = None,
    ) -> str:
        """Жёсткое ожидание свежей картинки. Берётся ТОЛЬКО:

        1) `<img>` из блока «Результат генерации», у которого src отличается
           от baseline и который полностью загружен (img.complete &&
           naturalWidth >= 200);
        2) либо самая последняя `<img>`, появившаяся в DOM ПОСЛЕ нажатия
           Generate, прошедшая ту же проверку.

        Если передан `net_events` (список (offset_sec, url) от listener'a
        «response», очищенный в момент клика Generate) — выбранный URL
        ДОПОЛНИТЕЛЬНО верифицируется: он должен быть в списке реально
        пришедших по сети image-ответов ПОСЛЕ клика. Это отсекает
        старые картинки из history/кэша, которые могут появиться в DOM при
        ререндере без реальной сетевой загрузки. Если net_events=None или
        пуст — верификация работает по старому (либеральному) пути.

        Никаких «возьму самую большую» — это и был источник косяков
        (приходила постер видосов / старая картинка из кэша). Если за
        timeout условие не сработало — кидаем OutseeImageError с подробным
        контекстом, что было/чего не хватило.
        """
        start = asyncio.get_event_loop().time()
        deadline = start + timeout
        last_log = 0.0
        last_seen_result: str | None = None

        while asyncio.get_event_loop().time() < deadline:
            now = asyncio.get_event_loop().time()
            elapsed = now - start

            # 1) приоритет — блок «Результат генерации»
            current = await self._result_img_src(page)
            last_seen_result = current
            if (
                current
                and current != baseline_result_img
                and not current.endswith("/placeholder.svg")
                and "data:image" not in current
                and not any(
                    m in current.lower() for m in _INPUT_REF_MARKERS
                )
            ):
                # Дополнительно проверим, что эта картинка действительно
                # новая (её не было в baseline-srcs), полностью загружена
                # и реально приходила по сети ПОСЛЕ клика Generate.
                if current not in baseline_all_srcs:
                    if await self._img_is_loaded(page, current):
                        if not _url_is_fresh(current, net_events):
                            # URL в DOM есть, но по сети он ещё не приходил
                            # (или это «old cached», подсунутый из history) —
                            # ждём дальше.
                            pass
                        else:
                            logger.info(
                                "_wait_image_url_strict: «Результат генерации» "
                                "за {:.0f} сек: {}",
                                elapsed,
                                current[:140],
                            )
                            return current

            # 2) фоллбэк — новая ПОЛНОСТЬЮ ЗАГРУЖЕННАЯ <img> в DOM,
            # которой не было в baseline. Берём ПОСЛЕДНЮЮ в порядке DOM,
            # при этом ОБЯЗАТЕЛЬНО фильтруя по net_events (если переданы)
            # — иначе легко подхватить старую картинку из history/cache.
            new_srcs = await self._completed_new_imgs(page, baseline_all_srcs)
            if new_srcs:
                # фильтруем UI-ассеты + входные референсы (см.
                # _INPUT_REF_MARKERS) по URL.
                clean = [
                    u
                    for u in new_srcs
                    if not any(m in u.lower() for m in _UI_ASSET_MARKERS)
                    and not any(m in u.lower() for m in _INPUT_REF_MARKERS)
                ]
                # Если включён net_events-фильтр (caller передал список) —
                # оставляем только URL'ы, реально пришедшие по сети ПОСЛЕ
                # клика Generate. См. `_url_is_fresh` — при net_events=None
                # фильтр прозрачный, при пустом списке отфильтрует всё.
                clean = [u for u in clean if _url_is_fresh(u, net_events)]
                if clean:
                    chosen = clean[-1]
                    logger.info(
                        "_wait_image_url_strict: новая <img> в DOM за "
                        "{:.0f} сек: {} (всего новых: {})",
                        elapsed,
                        chosen[:140],
                        len(clean),
                    )
                    return chosen

            # 2.5) Детект плашки «Контент отклонён» (модерация).
            # Outsee показывает её прямо на странице — ждать дальше
            # бесполезно: токены уже возвращены, генерации не будет.
            #
            # ВАЖНО: не считаем «отклонёнкой» плашку, которая уже была
            # видна на странице ДО клика Generate (например, осталась
            # от предыдущего запроса в history-сайдбаре). Только новая
            # плашка / другой текст. Иначе на свежеоткрытой странице
            # с историей мы сразу же ловим old-rejection и репортим её
            # как сегодняшнюю ошибку (выглядит как «43мс — отклонено»).
            #
            # Дополнительно: первые 3 секунды после клика игнорируем
            # детект совсем — outsee.io обычно успевает показать spinner,
            # а если плашка появляется правда — она будет видна и через
            # 3 секунды.
            if elapsed >= 3.0:
                rejected_text = await self._content_rejected_text(page)
                if rejected_text and rejected_text != pre_rejected_text:
                    raise OutseeContentRejectedError(
                        "outsee image: контент отклонён модерацией",
                        context={
                            "gen_id": gen_id,
                            "rejection": rejected_text[:200],
                        },
                    )

            # 3) diagnostic
            if elapsed - last_log > 15:
                last_log = elapsed
                n_big = len(await self._all_big_imgs(page))
                logger.info(
                    "_wait_image_url_strict: ждём... {:.0f} сек, "
                    "result_img_src={}, big_imgs_now={} (baseline={})",
                    elapsed,
                    (current[:80] if current else None),
                    n_big,
                    len(baseline_big_imgs),
                )

            await asyncio.sleep(1.0)

        # timeout — собираем диагностический контекст
        big_now = set(await self._all_big_imgs(page))
        new_big = big_now - baseline_big_imgs
        all_now_srcs = set(await self._all_img_srcs(page))
        new_dom = all_now_srcs - baseline_all_srcs
        raise OutseeImageError(
            f"outsee image: результат не появился за {int(timeout)} сек",
            context={
                "gen_id": gen_id,
                "baseline_result_img": baseline_result_img,
                "last_result_img_src": last_seen_result,
                "new_big_imgs": ", ".join(list(new_big)[:3]) or "—",
                "new_dom_srcs_count": len(new_dom),
                "baseline_big_imgs": len(baseline_big_imgs),
            },
        )

    async def _attach_ref_image_robust(
        self,
        page: Page,
        image_path: Path,
        *,
        where: str,
    ) -> bool:
        """Робастная загрузка референсной картинки в input[type=file]
        на странице outsee.io.

        Перед загрузкой ОЧИЩАЕМ ВСЕ input[type=file] (set_input_files([])
        каждому), чтобы не получить «стэкинг» референсов: в outsee
        страница может переиспользоваться между генерациями (`reuse=True`),
        и старый прикреплённый файл в input может остаться. Если на v=3
        мы просто кинем set_input_files на last input, а в first input
        ещё лежит файл от v=2 — outsee может прикрепить ОБА. См.
        пользовательский баг «3-я генерация прикрепляет реф снова».

        Порядок попыток:
          1) clear all: set_input_files([]) на каждый input[type=file] в DOM.
          2) Видимый input[type=file] (через _first_visible) — редко бывает,
             но пусть будет. Быстрый path.
          3) ЛЮБОЙ input[type=file] в DOM (вкл. скрытый). Playwright
             `set_input_files` работает на скрытых input'ах тоже —
             он не требует видимости. Берём ПОСЛЕДНИЙ (он в outsee
             обычно и есть «видимый для юзера» — прикрепленный к самой
             последней кнопке на экране).

        Возвращает True в случае успеха. False — если input вообще не
        нашлся в DOM или set_input_files упал. Свои dump'ы НЕ снимает —
        это решает вызывающий (у него список `dumps`).
        """
        # 0) очистка всех input[type=file] (на случай переиспользования
        # страницы — старый референс мог остаться от предыдущей генерации).
        try:
            base_clear = page.locator("input[type='file']")
            n_clear = await base_clear.count()
        except Exception:  # noqa: BLE001
            n_clear = 0
        if n_clear > 0:
            cleared = 0
            for i in range(n_clear):
                try:
                    await base_clear.nth(i).set_input_files([])
                    cleared += 1
                except Exception:  # noqa: BLE001
                    # некоторые input'ы могут быть недоступны для clear
                    # (например, отвязаны от формы) — пропускаем.
                    pass
            if cleared > 0:
                logger.info(
                    "outsee.{}: очищено {}/{} input[type=file] перед "
                    "загрузкой нового референса",
                    where, cleared, n_clear,
                )

        # 1) видимый input[type=file] (короткий таймаут — в outsee он почти
        # всегда скрыт, ожидать видимость долго нет смысла).
        file_sel = await _first_visible(
            page, FILE_UPLOAD_SELECTORS, timeout_ms=2_000
        )
        if file_sel:
            try:
                await page.locator(file_sel).first.set_input_files(
                    str(image_path)
                )
                logger.info(
                    "outsee.{}: reference {} загружен в видимый input ({})",
                    where, image_path.name, file_sel,
                )
                await asyncio.sleep(1.0)
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "outsee.{}: видимый input set_input_files упал: {}",
                    where, e,
                )

        # 2) Скрытый/свёрнутый input. set_input_files работает без видимости.
        try:
            base = page.locator("input[type='file']")
            count = await base.count()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.{}: locator('input[type=file]').count() упал: {}",
                where, e,
            )
            count = 0
        if count <= 0:
            logger.warning(
                "outsee.{}: input[type=file] не найден в DOM при попытке "
                "загрузить референс {}",
                where, image_path.name,
            )
            return False
        # Берём последний input[type=file] — в outsee.io именно он
        # привязан к UI-кнопке загрузки референса (предыдущие обычно
        # для иных фич, вроде формы регистрации).
        try:
            await base.last.set_input_files(str(image_path))
            logger.info(
                "outsee.{}: reference {} загружен в скрытый input "
                "(input[type=file] count={}, взят last)",
                where, image_path.name, count,
            )
            await asyncio.sleep(1.0)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.{}: set_input_files в скрытый input упал: {} "
                "(всего input[type=file] = {})",
                where, e, count,
            )
            return False

    async def _img_is_loaded(self, page: Page, src: str) -> bool:
        """Проверяет, что `<img>` с таким src уже полностью загружена."""
        try:
            return bool(
                await page.evaluate(
                    """(src) => {
                        const img = Array.from(document.querySelectorAll('img'))
                            .find(i => i.src === src);
                        if (!img) return false;
                        return img.complete && (img.naturalWidth || 0) >= 200;
                    }""",
                    src,
                )
            )
        except Exception:  # noqa: BLE001
            return False

    async def _content_rejected_text(self, page: Page) -> str | None:
        """Если на странице ВИДИМО показана плашка «Контент отклонён» —
        возвращает её текст, иначе None.

        Видимость проверяется строго: display!=none, visibility!=hidden,
        opacity>0, getBoundingClientRect>0, элемент в viewport, и все
        предки тоже видимы. Без этого outsee даёт false-positive: их
        React-bundle пререндерит шаблоны ошибок (`отклонённый контент /
        запрещённые слова`) как невидимые компоненты с ненулевым rect."""
        try:
            text = await page.evaluate(
                """() => {
                    const triggers = [
                        'Контент отклон',
                        'Content reject',
                        'не прошёл модер',
                        'содержит запрещ',
                        'forbidden word',
                    ];
                    function isTrulyVisible(el) {
                        const cs = window.getComputedStyle(el);
                        if (cs.display === 'none') return false;
                        if (cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
                        if (parseFloat(cs.opacity) === 0) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) return false;
                        if (r.bottom <= 0 || r.right <= 0) return false;
                        if (r.top >= window.innerHeight) return false;
                        if (r.left >= window.innerWidth) return false;
                        let p = el.parentElement;
                        while (p) {
                            const pcs = window.getComputedStyle(p);
                            if (pcs.display === 'none') return false;
                            if (pcs.visibility === 'hidden' || pcs.visibility === 'collapse') return false;
                            if (parseFloat(pcs.opacity) === 0) return false;
                            p = p.parentElement;
                        }
                        return true;
                    }
                    const all = Array.from(document.querySelectorAll('*'));
                    for (const el of all) {
                        const tag = (el.tagName || '').toLowerCase();
                        if (tag === 'textarea' || tag === 'input' || tag === 'script' || tag === 'style' || tag === 'template') continue;
                        const t = (el.textContent || '').trim();
                        if (!t || t.length > 1000) continue;
                        const low = t.toLowerCase();
                        let hit = false;
                        for (const tr of triggers) {
                            if (low.includes(tr.toLowerCase())) {
                                hit = true; break;
                            }
                        }
                        if (!hit) continue;
                        if (!isTrulyVisible(el)) continue;
                        return t.slice(0, 300);
                    }
                    return null;
                }"""
            )
            if isinstance(text, str) and text.strip():
                return text.strip()
        except Exception:  # noqa: BLE001
            pass
        return None

    # ----- VIDEO (veo-3-fast Relax) -----

    async def generate_video(
        self,
        prompt: str,
        out_path: Path,
        *,
        start_frame: Path | None = None,
        aspect_ratio: str = "9:16",
        timeout: float = 900,
        model_slug: str | None = None,
        resolution: str | None = None,
        relax: bool = False,
        prompt_id_prefix: str | None = None,
    ) -> GenerationResult:
        if prompt_id_prefix:
            prompt = f"{prompt_id_prefix}\n\n{(prompt or '').lstrip()}"
            logger.info(
                "outsee.generate_video: prompt_id_prefix={}", prompt_id_prefix
            )
        page_url = _video_page_url(model_slug)
        logger.info("outsee.generate_video: open url={}", page_url)
        page = await self.session.open_page(page_url, reuse=True)
        # ВАЖНО: всегда reload, чтобы сбросить состояние от предыдущей
        # генерации — иначе на ретрае останутся форма + start_frame +
        # возможная плашка ошибки от прошлой попытки.
        try:
            await page.goto(page_url, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.generate_video: page.goto({}) упал: {} — продолжаю "
                "без явного reload", page_url, e,
            )
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

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

        # 2) аспект (с верификацией состояния)
        if aspect_ratio:
            await _select_aspect_ratio(
                page, aspect_ratio, where="generate_video"
            )

        # 2.5) разрешение 720p / 1080p (best-effort)
        if resolution:
            res_sel = await _first_visible(
                page, _resolution_selectors(resolution), timeout_ms=3_000
            )
            if res_sel:
                try:
                    await page.locator(res_sel).first.click()
                    logger.info(
                        "outsee.generate_video: {} выбран", resolution
                    )
                except Exception:  # noqa: BLE001
                    pass

        # 2.7) Relax (только для veo-3-1-fast по словам пользователя)
        await _toggle_relax(page, want_on=relax, where="generate_video")

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


async def _download_via_context(
    page: Page,
    url: str,
    out_path: Path,
    *,
    timeout_ms: int = 120_000,
    attempts: int = 3,
) -> None:
    """Скачивает файл по URL, используя тот же контекст (cookies/auth) страницы.
    CDN outsee/hailuoai иногда медленный — поднимаем таймаут до 120 сек и
    делаем до 3 попыток."""
    ctx = page.context
    api = ctx.request
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            resp = await api.get(url, timeout=timeout_ms)
            if resp.status >= 400:
                raise RuntimeError(f"download {url} failed: HTTP {resp.status}")
            body = await resp.body()
            out_path.write_bytes(body)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            logger.warning(
                "_download_via_context: попытка {}/{} упала: {}",
                i,
                attempts,
                type(e).__name__,
            )
            await asyncio.sleep(1.5 * i)
    assert last is not None
    raise last


# ---------- recon util: python -m app.bots.outsee recon-image "prompt" ----------

async def _recon(kind: str, prompt: str, start_frame: str | None = None) -> None:
    url = settings.outsee_image_url if kind == "image" else settings.outsee_video_url
    async with browser_session() as bs:
        page = await bs.open_page(url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        # Ждём окончания сетевой активности (Next.js гидратация).
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
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
