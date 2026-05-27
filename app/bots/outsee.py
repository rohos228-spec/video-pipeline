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
import contextlib
import re
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
    project_id: int | None = None,
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
    from app.services.step_cancel import sleep_cancellable

    from app.services.step_cancel import abort_if_cancelled

    abort_if_cancelled(project_id)

    # 1) Сначала пробуем NEW UI: dropdown «Соотношение N:M».
    opener_sel = await _first_visible(
        page, ASPECT_DROPDOWN_OPENER_SELECTORS, timeout_ms=2_000, project_id=project_id
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
            await sleep_cancellable(0.3, project_id)
            opt_sel = await _first_visible(
                page, _aspect_option_selectors(ratio), timeout_ms=4_000, project_id=project_id
            )
            if opt_sel:
                try:
                    await page.locator(opt_sel).first.click(timeout=3_000)
                    logger.info(
                        "outsee.{}: aspect {} — выбран в dropdown ({})",
                        where, ratio, opt_sel,
                    )
                    await sleep_cancellable(0.3, project_id)
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
    sel = await _first_visible(page, _aspect_selectors(ratio), timeout_ms=4_000, project_id=project_id)
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

    await sleep_cancellable(0.3, project_id)
    ok = await _is_aspect_selected(page, sel)
    if ok is True:
        logger.info(
            "outsee.{}: aspect {} подтверждён выбран (sel={})",
            where, ratio, sel,
        )
        return True
    try:
        sel2 = await _first_visible(
            page, _aspect_selectors(ratio), timeout_ms=2_000, project_id=project_id
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
    project_id: int | None = None,
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
        page, LIMIT_TOGGLE_SELECTORS, timeout_ms=1_500, project_id=project_id
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
                    "кликаю (physical)",
                    where, "ON" if want_on else "OFF",
                )
            loc = page.locator(limit_sel).first
            await _physical_mouse_click(
                page, loc, project_id=project_id, label=f"{where} Безлимит"
            )
            await asyncio.sleep(0.45)
            after = await _read_limit_toggle_on(page, limit_sel)
            logger.info(
                "outsee.{}: Безлимит physical click → want={}, read={}",
                where,
                want_on,
                after,
            )
            if after == want_on:
                return
            # Повтор CDP по bbox
            box = await loc.bounding_box()
            if box:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                await _cdp_dispatch_click(
                    page, cx, cy, project_id=project_id
                )
                await asyncio.sleep(0.45)
                if await _read_limit_toggle_on(page, limit_sel) == want_on:
                    return
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.{}: тогл «Безлимит» поломался: {}", where, e,
            )

    # 2) Fallback: старые «Relax»-селекторы.
    sel = await _first_visible(page, RELAX_SELECTORS, timeout_ms=2_000, project_id=project_id)
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
        await _physical_mouse_click(
            page, loc, project_id=project_id, label=f"{where} Relax"
        )
        logger.info(
            "outsee.{}: Relax physical {} (sel={})",
            where,
            "ON" if want_on else "OFF",
            sel,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("outsee.{}: Relax toggle упал: {}", where, e)


async def _scan_toggle_targets(
    page: Page, *, keywords: list[str]
) -> list[dict[str, Any]]:
    try:
        raw = await page.evaluate(
            """(keys) => {
                const out = [];
                for (const el of document.querySelectorAll(
                    'button, [role="switch"], label, [role="button"]'
                )) {
                    const text = (el.innerText || el.textContent || '').trim();
                    const low = text.toLowerCase();
                    if (!keys.some(k => low.includes(k))) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 24 || r.height < 14) continue;
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    out.push({
                        text: text.slice(0, 48),
                        cx: Math.round(r.x + r.width / 2),
                        cy: Math.round(r.y + r.height / 2),
                        area: Math.round(r.width * r.height),
                    });
                }
                out.sort((a, b) => b.area - a.area);
                return out;
            }""",
            [k.lower() for k in keywords],
        )
        return list(raw or [])
    except Exception:  # noqa: BLE001
        return []


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


# Маркеры видимых плашек ошибок outsee (см. `_detect_outsee_failure`).
_OUTSEE_MODERATION_MARKERS: tuple[str, ...] = (
    "контент отклон",
    "content reject",
    "не прошёл модер",
    "содержит запрещ",
    "forbidden word",
)
_OUTSEE_GENERATION_ERROR_MARKERS: tuple[str, ...] = (
    "ошибка генера",
    "ошибк",  # «Ошибка», «Произошла ошибка»
    "не удалось сгенер",
    "не удалось создать",
    "generation failed",
    "failed to generate",
    "something went wrong",
    "что-то пошло не так",
    "попробуйте снова",
    "повторите попытку",
    "try again",
    "unable to generate",
)


def _outsee_failure_kind(text: str) -> str:
    """`moderation` | `generation` | `unknown` (видимая плашка без точного класса)."""
    low = text.lower()
    for m in _OUTSEE_MODERATION_MARKERS:
        if m in low:
            return "moderation"
    for m in _OUTSEE_GENERATION_ERROR_MARKERS:
        if m in low:
            return "generation"
    return "unknown"


def _raise_outsee_failure(
    *,
    text: str,
    gen_id: str,
    elapsed: float,
    in_result: bool,
) -> None:
    kind = _outsee_failure_kind(text)
    ctx = {
        "gen_id": gen_id,
        "failure": text[:200],
        "elapsed_sec": round(elapsed, 1),
        "in_result_panel": in_result,
        "kind": kind,
    }
    if kind == "moderation":
        raise OutseeContentRejectedError(
            "outsee image: контент отклонён модерацией",
            context=ctx,
        )
    raise OutseeImageError(
        "outsee image: ошибка генерации на outsee.io",
        context=ctx,
    )


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
#
# `videomobilepreview/topaz.webp` — outsee.io кладёт сюда статус-плашку
# «идёт обработка через Topaz» (тёмный фон + три белых квадрата
# в виде loading-анимации). Если её НЕ отфильтровать на уровне URL,
# `_wait_image_url_strict` радостно вернёт этот URL как «новая
# картинка в DOM» (она и правда новая), валидатор размера её
# не ловит (webp-анимация ~80КБ), и она сохраняется как hero.png.
# Юзер потом видит лоадер вместо персонажа.
_UI_ASSET_MARKERS = (
    "/_next/",
    "/static/",
    "/assets/",
    "/icons/",
    "/logo",
    "favicon",
    "sprite",
    "/videomobilepreview/",
    "topaz.webp",
    "/preview/loader",
    "/skeleton",
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


def _strip_url_query(url: str | None) -> str:
    """Снимает `?query` и `#fragment`, оставляет scheme+host+path.

    Outsee.io хранит thumb'ы в Yandex Cloud Storage с подписанными URL
    `…image_X.jpg?X-Amz-Algorithm=...&X-Amz-Signature=…`. Подписи
    перевыпускаются на каждом ререндере страницы, поэтому одна и та же
    галерейная картинка в baseline и в DOM после Generate имеет РАЗНЫЕ
    URL-строки — без нормализации все галерейные thumb'ы фолсли
    помечались «новыми» и `clean[-1]/clean[0]` уносил случайную старую.

    Stable-идентификатор картинки = host+path, потому что
    `image_<ts>_<idx>_thumb.jpg` уникален на стороне outsee и не меняется
    между перевыпусками подписи.
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:  # noqa: BLE001
        # На совсем странных URL'ах оставляем как есть.
        i = url.find("?")
        if i >= 0:
            url = url[:i]
        i = url.find("#")
        if i >= 0:
            url = url[:i]
        return url


def _newest_fresh_url(
    net_events: list[tuple[float, str]] | None,
    *,
    baseline_srcs: set[str] | None = None,
) -> str | None:
    """Return the most recent image URL from net_events that is NOT in baseline.

    Uses the offset_sec timestamps stored in net_events to pick the
    freshest image response.  If baseline_srcs is given, URLs whose
    normalized form appears in it are excluded.
    """
    if not net_events:
        return None
    skip = baseline_srcs or set()
    candidates = [
        (ts, url) for ts, url in net_events
        if url
        and _strip_url_query(url) not in skip
        and not any(m in url.lower() for m in _UI_ASSET_MARKERS)
        and not any(m in url.lower() for m in _INPUT_REF_MARKERS)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


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


# Минимум «настоящего» mp4 от veo — обычно сотни KB+.
_MIN_VIDEO_BYTES = 80_000


def _is_candidate_video_response(resp: Any) -> bool:
    """Сетевой ответ похож на результат veo: video/* или mp4, не UI-ассет."""
    try:
        url = resp.url or ""
        ct = (resp.headers.get("content-type") or "").lower()
        low = url.lower()
        is_video_ct = ct.startswith("video/")
        is_mp4_url = ".mp4" in low or "/video/" in low
        if not is_video_ct and not is_mp4_url:
            return False
        if any(marker in low for marker in _UI_ASSET_MARKERS):
            return False
        if any(marker in low for marker in _INPUT_REF_MARKERS):
            return False
        cl = resp.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) < _MIN_VIDEO_BYTES:
                    return False
            except ValueError:
                pass
        return True
    except Exception:  # noqa: BLE001
        return False


def _video_url_looks_like_result(url: str | None) -> bool:
    if not url or url.startswith("data:"):
        return False
    low = url.lower()
    if any(marker in low for marker in _UI_ASSET_MARKERS):
        return False
    if any(marker in low for marker in _INPUT_REF_MARKERS):
        return False
    return True


async def _first_visible(
    page: Page, selectors: list[str], *, timeout_ms: int = 15_000, project_id: int | None = None
) -> str | None:
    """Возвращает CSS-селектор с уже вставленным `:nth-match(sel, N)`, который
    гарантированно попадает в первый ВИДИМЫЙ элемент. Страницы outsee часто
    рендерят 2–3 копии одного textarea (desktop + mobile + sidebar), и
    locator(sel).first может ткнуть в скрытую."""
    from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        abort_if_cancelled(project_id)
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
        await sleep_cancellable(0.3, project_id)
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
        reference_image: Path | list[Path] | None = None,
        project_id: int | None = None,
    ) -> GenerationResult:
        """Генерирует картинку на outsee.io.

        Параметры:
          model_slug      — slug для URL (`?model=<slug>`). Если None —
                            используется settings.outsee_image_url как есть.
          aspect_ratio    — строка-ярлык кнопки («16:9», «9:16»…). Жмём
                            кнопку и проверяем состояние.
          resolution      — строка-ярлык («2K» / «4K»). Best-effort клик.
          relax           — если True и Relax-кнопка есть на странице — включаем.
          reference_image — если передан Path или list[Path] — загружаем
                            картинку(и) как референс(ы) для генерации
                            (через input[type=file] на странице outsee.io).
                            Используется в hero-вариациях (1 ref персонажа)
                            и в шаге 8 «Картинки» (до 2 ref: персонаж +
                            предмет, читаются из xlsx R38/R39 для кадра).
          prompt_id_prefix — строка вида `[ID: P12-F3-a7f2b01c]`. Будет
                             поставлена ПЕРВОЙ строкой промта, чтобы в
                             истории outsee однозначно отличать эту
                             генерацию от всех прошлых.
        """
        import time as _time
        import uuid as _uuid

        from app.services.step_cancel import abort_if_cancelled, await_with_cancel

        abort_if_cancelled(project_id)
        gen_id = gen_id or _uuid.uuid4().hex
        if prompt_id_prefix:
            from app.generation_options import prepend_gen_id

            prompt = prepend_gen_id(prompt, prompt_id_prefix)
            logger.info(
                "outsee.generate_image: prompt_id_prefix={} [download-v3: wait→10img]",
                prompt_id_prefix,
            )
        _verify_prompt_length_before_send(prompt, where="generate_image")

        page_url = _image_page_url(model_slug)
        logger.info(
            "outsee.generate_image: открываю страницу gen_id={} url={}",
            gen_id[:8], page_url,
        )
        page = await self.session.open_page(page_url, reuse=True)
        from app.services.step_cancel import register_active_page, unregister_active_page

        if project_id is not None:
            register_active_page(project_id, page)
        try:
            return await self._generate_image_on_page(
                page,
                prompt=prompt,
                out_path=out_path,
                aspect_ratio=aspect_ratio,
                timeout=timeout,
                gen_id=gen_id,
                model_slug=model_slug,
                resolution=resolution,
                relax=relax,
                prompt_id_prefix=prompt_id_prefix,
                reference_image=reference_image,
                project_id=project_id,
                page_url=page_url,
            )
        finally:
            if project_id is not None:
                unregister_active_page(project_id)

    async def _generate_image_on_page(
        self,
        page: Any,
        *,
        prompt: str,
        out_path: Path,
        aspect_ratio: str,
        timeout: float,
        gen_id: str,
        model_slug: str | None,
        resolution: str | None,
        relax: bool,
        prompt_id_prefix: str | None,
        reference_image: Path | list[Path] | None,
        project_id: int | None,
        page_url: str,
    ) -> GenerationResult:
        """Тело generate_image — отдельно, чтобы register_active_page в finally."""
        import time as _time
        import uuid as _uuid

        from app.services.step_cancel import abort_if_cancelled, await_with_cancel

        abort_if_cancelled(project_id)
        dumps: list[Path] = []
        try:
            await await_with_cancel(
                page.goto(page_url, wait_until="domcontentloaded"), project_id
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.generate_image: page.goto({}) упал: {} — продолжаю "
                "без явного reload", page_url, e,
            )
        await await_with_cancel(page.wait_for_load_state("domcontentloaded"), project_id)
        try:
            await await_with_cancel(
                page.wait_for_load_state("networkidle", timeout=15_000), project_id
            )
        except Exception:
            pass
        abort_if_cancelled(project_id)
        logger.info("outsee.generate_image: страница готова, гидрация ok")

        # Снимок «до» — все большие картинки и URL-ы, которые уже на странице.
        # Свежим результатом считаем ТОЛЬКО то, чего тут не было.
        # ВАЖНО: храним baseline как НОРМАЛИЗОВАННЫЕ host+path (без
        # `?X-Amz-Signature=...`). Иначе re-sign на каждом ререндере
        # делает все галерейные thumb'ы «новыми» и бот хватает старую
        # картинку из истории outsee.
        baseline_result_img = _strip_url_query(await self._result_img_src(page))
        baseline_big_imgs = {
            _strip_url_query(u) for u in await self._all_big_imgs(page)
        }
        baseline_dom_srcs = {
            _strip_url_query(u) for u in await self._all_img_srcs(page)
        }
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
            pre_rejected_text: str | None = None

            # 1) вбить промт — всегда (как в рабочем TG-боте до anti-dup skip).
            input_sel = await _first_visible(
                page, PROMPT_INPUT_SELECTORS, timeout_ms=60_000, project_id=project_id
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
                await await_with_cancel(
                    page.locator(input_sel).first.scroll_into_view_if_needed(
                        timeout=5_000
                    ),
                    project_id,
                )
            except Exception:  # noqa: BLE001
                pass
            await await_with_cancel(page.locator(input_sel).first.click(), project_id)
            await await_with_cancel(page.locator(input_sel).first.fill(prompt), project_id)
            await _verify_composer_prompt_filled(
                page,
                input_sel,
                expected_prompt=prompt,
                prompt_id_prefix=prompt_id_prefix,
                where="generate_image",
            )
            actual_len = len(await _read_composer_prompt_value(page, input_sel))
            logger.info(
                "outsee.generate_image: промт в поле ввода (отправлено {} симв, "
                "в textarea {} симв)",
                len(prompt),
                actual_len,
            )
            abort_if_cancelled(project_id)

            gen_probe = await _first_visible(
                page,
                GENERATE_BUTTON_SELECTORS[:6],
                timeout_ms=3_000,
                project_id=project_id,
            )
            if gen_probe and await page.locator(gen_probe).first.is_disabled():
                raise OutseeImageError(
                    "outsee: кнопка Generate заблокирована — промт не принят",
                    context={
                        "gen_id": gen_id,
                        "prompt_len": len(prompt),
                        "composer_len": actual_len,
                    },
                    dumps=dumps,
                )

            # 2) выбрать aspect ratio (поддержка любого W:H, с верификацией)
            if aspect_ratio:
                await _select_aspect_ratio(
                    page, aspect_ratio, where="generate_image", dumps=dumps,
                    project_id=project_id,
                )

            # 2.5) выбрать разрешение 2K / 4K (best-effort)
            if resolution:
                res_sel = await _first_visible(
                    page, _resolution_selectors(resolution), timeout_ms=3_000,
                    project_id=project_id,
                )
                if res_sel:
                    try:
                        await await_with_cancel(
                            page.locator(res_sel).first.click(), project_id
                        )
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
                project_id=project_id,
            )
            abort_if_cancelled(project_id)

            # 2.9) Reference-картинка (для hero-вариаций 2..N).
            # На странице outsee.io image обычно есть СКРЫТЫй input[type=file]
            # для подгрузки референса. Обычный _first_visible его НЕ найдёт
            # (видимость=False), поэтому используем робастный хелпер
            # `_attach_ref_image_robust`: он первым делом пытается видимый
            # input, иначе берёт ЛЮБОЙ input[type=file] в DOM и бьёт
            # set_input_files в него (по Playwright он работает на скрытых тоже).
            if reference_image is not None:
                # Поддержка single Path и list[Path]: для шага 8 «Картинки»
                # передаётся [персонаж.png, предмет.png] (до 2 ref). Для
                # старого hero-flow — один Path.
                refs: list[Path] = (
                    [reference_image]
                    if isinstance(reference_image, Path)
                    else list(reference_image)
                )
                for ref_idx, ref_path in enumerate(refs, start=1):
                    if not ref_path.exists():
                        logger.warning(
                            "outsee.generate_image: reference_image #{} {} "
                            "не найден на диске",
                            ref_idx, ref_path,
                        )
                        continue
                    attached = await self._attach_ref_image_robust(
                        page, ref_path,
                        where=f"generate_image[ref{ref_idx}]",
                        project_id=project_id,
                    )
                    if not attached:
                        h, p = await _dump_page(
                            page, f"ref_input_notfound_{ref_idx}"
                        )
                        for x in (h, p):
                            if x:
                                dumps.append(x)

            # 3) кнопка generate
            gen_sel = await _first_visible(
                page, GENERATE_BUTTON_SELECTORS, timeout_ms=10_000, project_id=project_id
            )
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
            await self._wait_button_enabled(
                page, gen_sel, timeout_s=600, project_id=project_id
            )
            abort_if_cancelled(project_id)

            # Re-baseline ПОСЛЕ всех настроек (aspect dropdown, разрешение,
            # Relax, референс) — клики по dropdown вызывают ререндер
            # правой панели и могут «принести» в DOM другую картинку,
            # которую мы иначе ошибочно посчитаем «новым результатом».
            # См. коммент выше про _strip_url_query.
            baseline_result_img = _strip_url_query(
                await self._result_img_src(page)
            )
            baseline_big_imgs = {
                _strip_url_query(u) for u in await self._all_big_imgs(page)
            }
            baseline_dom_srcs = {
                _strip_url_query(u) for u in await self._all_img_srcs(page)
            }
            logger.info(
                "outsee.generate_image: re-baseline перед Generate "
                "result_img={}, big_imgs={}, all_imgs={}",
                (baseline_result_img[:80] if baseline_result_img else None),
                len(baseline_big_imgs),
                len(baseline_dom_srcs),
            )

            # ВАЖНО: после re-baseline нужно повторно исключить URL
            # уже-существующей карточки из baseline. Re-baseline
            # пересобрал множества с нуля, и если карточка дошла
            # до полной отрисовки между initial baseline и re-baseline
            # — её URL снова попадёт в baseline_*. Делаем это ПОСЛЕ
            # re-baseline, чтобы исключение точно сработало.
            # (В ветке `already_in_progress` мы сюда не заходим —
            # re-baseline не делается, baseline_* уже почищены.)

            # Снимок видимой плашки ошибки ДО клика Generate (модерация или
            # «ошибка генерации»). Outsee часто оставляет остаток от прошлой
            # попытки в history/result — передаём в wait, чтобы не путать
            # с НОВОЙ ошибкой, но повтор той же плашки в result-панели после
            # клика всё равно считаем свежим сбоем.
            pre_rejected_text = await self._outsee_failure_text(page)
            if pre_rejected_text:
                logger.info(
                    "outsee.generate_image: pre-click failure_text"
                    " обнаружена ({} симв, kind={}) — baseline для детектора",
                    len(pre_rejected_text),
                    _outsee_failure_kind(pre_rejected_text),
                )

            # Install a MutationObserver to catch newly added <img>
            # elements. The observer stores URLs of images added to DOM
            # after Generate is clicked. This provides an independent
            # signal that complements the polling-based detection.
            if prompt_id_prefix:
                try:
                    await page.evaluate(
                        """() => {
                            window.__vp_new_imgs = [];
                            window.__vp_observer = new MutationObserver((mutations) => {
                                for (const m of mutations) {
                                    for (const node of m.addedNodes) {
                                        if (!node || !node.querySelectorAll) continue;
                                        const imgs = node.tagName === 'IMG'
                                            ? [node]
                                            : node.querySelectorAll('img');
                                        for (const img of imgs) {
                                            if (img.src && !img.src.startsWith('data:')
                                                && img.naturalWidth >= 100) {
                                                window.__vp_new_imgs.push(img.src);
                                            }
                                        }
                                    }
                                }
                            });
                            window.__vp_observer.observe(
                                document.body,
                                {childList: true, subtree: true}
                            );
                        }"""
                    )
                except Exception:  # noqa: BLE001
                    pass

            # Anti-duplicate check: if our prompt_id_prefix already
            # appears in the gallery (from a previous failed attempt on
            # the same page), log a warning. The _uniquify_prompt_id in
            # outsee_retry should prevent this, but belt-and-suspenders.
            if prompt_id_prefix:
                try:
                    id_tokens_check = _prompt_id_search_tokens(prompt_id_prefix)
                    pre_gen_count = await self._count_id_tokens_in_page(
                        page, id_tokens_check
                    )
                    pre_total = sum(pre_gen_count.values())
                    if pre_total > 0:
                        page_text_check = await _page_text_excluding_composer(page)
                        in_non_composer = any(
                            tok in page_text_check
                            for tok in id_tokens_check if tok
                        )
                        logger.warning(
                            "outsee.generate_image: pre-Generate ID {} "
                            "уже найден на странице (total={}, "
                            "in_non_composer={}). Anti-dup: retry ID "
                            "будет уникальным через _uniquify_prompt_id.",
                            prompt_id_prefix,
                            pre_total,
                            in_non_composer,
                        )
                except Exception:  # noqa: BLE001
                    pass

            click_ts = _time.monotonic()
            net_events.clear()
            await await_with_cancel(page.locator(gen_sel).first.click(), project_id)
            logger.info(
                "outsee.generate_image: Generate кликнут, жду картинку (gen_id={})",
                    gen_id[:8],
                )

            # 4) строгое ожидание свежей картинки.
            # Передаём prompt_id_prefix — `_wait_image_url_strict` тогда
            # ищет в DOM карточку именно с НАШИМ `[ID: P1-HERO1-V1-…]`
            # и игнорирует все остальные (старые/чужие фото из истории
            # outsee). Это самая строгая верификация — без неё бот мог
            # подхватить чужой/прошлый результат, см. баг с
            # «загрузило старую фотку с другим идентификатором».
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
                    prompt_id_prefix=prompt_id_prefix,
                    project_id=project_id,
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
            # Cleanup MutationObserver
            with contextlib.suppress(Exception):
                await page.evaluate(
                    """() => {
                        if (window.__vp_observer) {
                            window.__vp_observer.disconnect();
                            delete window.__vp_observer;
                        }
                        delete window.__vp_new_imgs;
                    }"""
                )

        # 5) скачиваем — клик по зелёной кнопке «↓» на НАШЕЙ карточке
        # (ID-привязка). Сам outsee отдаёт реальный финальный файл —
        # это исключает все косяки с topaz.webp / input_*.png / svg-
        # плейсхолдерами, которые подсовывал старый URL-путь.
        # Если prompt_id_prefix не передан (legacy / recon-mode) —
        # фолбэк на старую URL-выкачку.
        try:
            if prompt_id_prefix:
                await _download_via_card_click(
                    page,
                    prompt_id_prefix=prompt_id_prefix,
                    out_path=out_path,
                    project_id=project_id,
                    img_url=img_url,
                )
            else:
                await _download_via_context(
                    page, img_url, out_path, project_id=project_id
                )
        except OutseeImageError as e:
            e.context.setdefault("gen_id", gen_id)
            e.context.setdefault("img_url", img_url)
            e.dumps = list(dumps)
            raise
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

        # 5.1) Валидация скачанного файла. С click-Download через
        # `expect_download()` подсунуть `topaz.webp` или
        # `input_*.png` outsee уже не сможет, но базовая проверка
        # (>50 KB + magic-байты PNG/JPEG/WebP) остаётся — на случай
        # битого CDN-ответа.
        try:
            _validate_downloaded_image(
                out_path, gen_id=gen_id, img_url=img_url
            )
        except OutseeImageError as e:
            e.dumps = list(dumps)
            raise

        # 5.2) Post-download uniqueness verification: confirm that our
        # prompt ID matches exactly 1 card in the gallery.  Multiple
        # matches indicate an ambiguity bug (e.g. retry token collision).
        # Uses _count_gallery_id_matches (gallery cards),
        # _page_text_excluding_composer + _count_tokens_in_text (page-wide),
        # and _diag_id_in_page (detailed diagnostics).
        if prompt_id_prefix:
            try:
                match_count = await _count_gallery_id_matches(
                    page, prompt_id_prefix
                )
                # Also count occurrences in page text (excluding composer)
                # to detect if ID appears in non-gallery elements.
                page_text = await _page_text_excluding_composer(page)
                id_tokens = _prompt_id_search_tokens(prompt_id_prefix)
                text_count = _count_tokens_in_text(page_text, id_tokens)

                if match_count == 0:
                    diag = await self._diag_id_in_page(
                        page, prompt_id_prefix
                    )
                    logger.warning(
                        "outsee image: post-download ID {} — 0 карточек "
                        "в галерее (text_count={}, diag={}). "
                        "Файл сохранён — продолжаем.",
                        prompt_id_prefix,
                        text_count,
                        diag,
                    )
                elif match_count > 1:
                    logger.warning(
                        "outsee image: post-download ID {} — {} карточек "
                        "в галерее (text_count={}) — неоднозначность!",
                        prompt_id_prefix,
                        match_count,
                        text_count,
                    )
                else:
                    logger.info(
                        "outsee image: post-download ID {} — "
                        "ровно 1 совпадение ✓ (text_count={})",
                        prompt_id_prefix,
                        text_count,
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "outsee image: post-download проверка упала: {}", e
                )

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
        project_id: int | None = None,
        prompt_id_prefix: str | None = None,
    ) -> GenerationResult:
        """Жмёт «Повторить» на существующем результате генерации — без ChatGPT,
        без перезаполнения промта. Сайт использует тот же промт и настройки.
        If prompt_id_prefix is provided, uses _download_via_card_click
        (same robust path as generate_image) instead of plain URL download."""
        import time as _time
        import uuid as _uuid

        from app.services.step_cancel import (
            abort_if_cancelled,
            await_with_cancel,
            register_active_page,
            unregister_active_page,
        )

        abort_if_cancelled(project_id)
        gen_id = gen_id or _uuid.uuid4().hex
        page = await self.session.open_page(settings.outsee_image_url, reuse=True)
        if project_id is not None:
            register_active_page(project_id, page)
        try:
            await await_with_cancel(
                page.wait_for_load_state("domcontentloaded"), project_id
            )
            try:
                await await_with_cancel(
                    page.wait_for_load_state("networkidle", timeout=15_000),
                    project_id,
                )
            except Exception:
                pass
            abort_if_cancelled(project_id)

            baseline_result_img = _strip_url_query(await self._result_img_src(page))
            baseline_big_imgs = {
                _strip_url_query(u) for u in await self._all_big_imgs(page)
            }
            baseline_dom_srcs = {
                _strip_url_query(u) for u in await self._all_img_srcs(page)
            }

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
                    project_id=project_id,
                )
                if not retry_sel:
                    raise OutseeImageError(
                        "outsee image: не найдена кнопка «Повторить» — на странице "
                        "нет предыдущего результата",
                        context={"gen_id": gen_id},
                    )
                try:
                    await await_with_cancel(
                        page.locator(retry_sel).first.scroll_into_view_if_needed(
                            timeout=5_000
                        ),
                        project_id,
                    )
                except Exception:  # noqa: BLE001
                    pass
                pre_rejected_text = await self._outsee_failure_text(page)
                click_ts = _time.monotonic()
                net_events.clear()
                await await_with_cancel(
                    page.locator(retry_sel).first.click(), project_id
                )
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
                    prompt_id_prefix=prompt_id_prefix,
                    project_id=project_id,
                )
            finally:
                try:
                    page.remove_listener("response", _on_response)
                except Exception:  # noqa: BLE001
                    pass

            out_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if prompt_id_prefix:
                    await _download_via_card_click(
                        page,
                        prompt_id_prefix=prompt_id_prefix,
                        out_path=out_path,
                        project_id=project_id,
                        img_url=img_url,
                    )
                else:
                    await _download_via_context(
                        page, img_url, out_path, project_id=project_id
                    )
            except OutseeImageError:
                raise
            except Exception as e:  # noqa: BLE001
                raise OutseeImageError(
                    "outsee image: скачивание результата (regenerate) упало",
                    context={
                        "gen_id": gen_id,
                        "img_url": img_url,
                        "err": f"{type(e).__name__}: {e}",
                    },
                ) from e
        finally:
            if project_id is not None:
                unregister_active_page(project_id)

        _validate_downloaded_image(out_path, gen_id=gen_id, img_url=img_url)

        if prompt_id_prefix:
            try:
                match_count = await _count_gallery_id_matches(
                    page, prompt_id_prefix
                )
                if match_count == 1:
                    logger.info(
                        "outsee regenerate: post-download ID {} — 1 совпадение ✓",
                        prompt_id_prefix,
                    )
                elif match_count > 1:
                    logger.warning(
                        "outsee regenerate: post-download ID {} — {} совпадений!",
                        prompt_id_prefix, match_count,
                    )
            except Exception:  # noqa: BLE001
                pass

        logger.info(
            "outsee image regenerated → {} (gen_id={})", out_path, gen_id[:8]
        )
        return GenerationResult(file_path=out_path, raw_url=img_url, gen_id=gen_id)

    async def _wait_button_enabled(
        self, page: Page, selector: str, *, timeout_s: float = 180, project_id: int | None = None
    ) -> None:
        """Ждёт пока кнопка станет активной (не disabled). На outsee Generate
        заблокирован, если идёт предыдущая генерация или пуст промт."""
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        deadline = asyncio.get_event_loop().time() + timeout_s
        last_log = 0.0
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
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
            await sleep_cancellable(1.0, project_id)
        raise PWTimeoutError(
            "outsee image: кнопка Generate остаётся disabled — "
            "предыдущая генерация зависла?"
        )

    async def _click_generate_button(
        self,
        page: Page,
        *,
        where: str,
        project_id: int | None = None,
        dumps: list[Path] | None = None,
        context: dict[str, object] | None = None,
        physical_only: bool = False,
    ) -> str:
        """Клик Generate: wait enabled + проверка что генерация стартовала.

        `physical_only=True` (видео): все попытки — реальная мышь по центру
        кнопки; JS/locator.click не используем — outsee veo часто его игнорит.
        """
        from app.services.step_cancel import abort_if_cancelled, await_with_cancel, sleep_cancellable

        abort_if_cancelled(project_id)
        gen_sel = await _first_visible(
            page, GENERATE_BUTTON_SELECTORS, timeout_ms=10_000, project_id=project_id
        )
        if not gen_sel:
            dump_paths: list[Path] = []
            h, p = await _dump_page(page, f"{where}_generate_notfound")
            for x in (h, p):
                if x:
                    dump_paths.append(x)
            if dumps is not None:
                dumps.extend(dump_paths)
            raise OutseeImageError(
                f"outsee {where}: не найдена кнопка Generate",
                context=context or {},
                dumps=dump_paths,
            )
        logger.info("outsee.{}: кнопка Generate найдена ({})", where, gen_sel)
        loc = page.locator(gen_sel).first
        try:
            await await_with_cancel(
                loc.scroll_into_view_if_needed(timeout=5_000), project_id
            )
        except Exception:  # noqa: BLE001
            pass
        await self._wait_button_enabled(
            page, gen_sel, timeout_s=600, project_id=project_id
        )
        abort_if_cancelled(project_id)

        for attempt in range(1, 4):
            abort_if_cancelled(project_id)
            use_physical = physical_only or attempt > 1
            if use_physical:
                if attempt > 1 or physical_only:
                    logger.info(
                        "outsee.{}: клик Generate мышью {}/3",
                        where,
                        attempt,
                    )
                await _physical_mouse_click(
                    page, loc, project_id=project_id, label=f"{where} Generate"
                )
            else:
                await await_with_cancel(loc.click(), project_id)
            await sleep_cancellable(1.0, project_id)
            if not await self._generate_button_enabled(page):
                logger.info(
                    "outsee.{}: Generate кликнут (gen busy), жду результат",
                    where,
                )
                return gen_sel
            logger.warning(
                "outsee.{}: Generate всё ещё активна после клика {}/3",
                where,
                attempt,
            )

        dump_paths: list[Path] = []
        h, p = await _dump_page(page, f"{where}_generate_stuck")
        for x in (h, p):
            if x:
                dump_paths.append(x)
        if dumps is not None:
            dumps.extend(dump_paths)
        raise OutseeImageError(
            f"outsee {where}: Generate не запустилась — кнопка осталась "
            "активной после 3 кликов (в т.ч. physical mouse)",
            context={
                **(context or {}),
                "physical_only": physical_only,
                "gen_sel": gen_sel,
            },
            dumps=dump_paths,
        )

    async def _generation_started(self, page: Page) -> bool:
        """True — outsee реально начал генерацию (не только «кликнули»).

        ТОЛЬКО надёжные сигналы:
          1) Кнопка Generate стала disabled / aria-disabled / aria-busy
          2) Видимый spinner (animate-spin)
          3) Прогресс-бар
        НЕ проверяем text body — слова «генерация», «loading» и т.п.
        встречаются в обычном UI outsee и дают ложноположительный сигнал.
        """
        try:
            via_dom = await page.evaluate(
                """() => {
                    // 1) Generate button disabled = generation running
                    for (const el of document.querySelectorAll(
                        'button, [role="button"]'
                    )) {
                        const tx = (el.innerText || el.textContent || '')
                            .toLowerCase().trim();
                        if (tx.length > 30) continue;
                        if (!tx.includes('генерир') && !tx.includes('generate')
                            && !tx.includes('создать')) continue;
                        if (el.disabled) return 'btn_disabled';
                        if (el.getAttribute('aria-disabled') === 'true') {
                            return 'btn_aria_disabled';
                        }
                        if (el.getAttribute('aria-busy') === 'true') {
                            return 'btn_busy';
                        }
                    }
                    // 2) Visible spinner
                    for (const el of document.querySelectorAll(
                        '[class*="animate-spin"], [class*="spinner"], '
                        + '[data-loading="true"]'
                    )) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 8 && r.height > 8) return 'spinner';
                    }
                    // 3) Progress bar
                    for (const el of document.querySelectorAll(
                        '[role="progressbar"], progress, '
                        + '[class*="progress"], [class*="Progress"]'
                    )) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 20 && r.height > 3) return 'progress';
                    }
                    return null;
                }"""
            )
            if via_dom:
                logger.info(
                    "outsee: generation_started signal ({})", via_dom
                )
                return True
        except Exception:  # noqa: BLE001
            pass
        return not await self._generate_button_enabled(page)

    async def _ensure_relax_for_video(
        self,
        page: Page,
        *,
        want_on: bool,
        where: str,
        project_id: int | None = None,
        dumps: list[Path] | None = None,
    ) -> None:
        """Relax/Безлимит для veo — physical click + проверка состояния."""
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        abort_if_cancelled(project_id)
        if not want_on:
            await _toggle_relax(
                page,
                want_on=False,
                where=where,
                dumps=dumps,
                project_id=project_id,
            )
            return

        for attempt in range(1, 4):
            abort_if_cancelled(project_id)
            await _toggle_relax(
                page,
                want_on=True,
                where=where,
                dumps=dumps,
                project_id=project_id,
            )
            limit_sel = await _first_visible(
                page, LIMIT_TOGGLE_SELECTORS, timeout_ms=3_000, project_id=project_id
            )
            if limit_sel:
                state = await _read_limit_toggle_on(page, limit_sel)
                if state is True:
                    logger.info(
                        "outsee.{}: Relax ON подтверждён (Безлимит, попытка {})",
                        where,
                        attempt,
                    )
                    return

            targets = await _scan_toggle_targets(
                page, keywords=["безлимит", "relax"]
            )
            logger.info(
                "outsee.{}: Relax scan → {} целей (попытка {})",
                where,
                len(targets),
                attempt,
            )
            for idx, t in enumerate(targets[:4]):
                cx, cy = float(t["cx"]), float(t["cy"])
                await _cdp_dispatch_click(page, cx, cy, project_id=project_id)
                await _viewport_mouse_click(
                    page, cx, cy, project_id=project_id, label=f"relax#{idx}"
                )
                await sleep_cancellable(0.5, project_id)
                if limit_sel and await _read_limit_toggle_on(page, limit_sel) is True:
                    logger.info(
                        "outsee.{}: Relax ON после scan-click #{}", where, idx
                    )
                    return

            await sleep_cancellable(0.8, project_id)

        dump_paths: list[Path] = []
        h, p = await _dump_page(page, "video_relax_not_on")
        for x in (h, p):
            if x:
                dump_paths.append(x)
        if dumps is not None:
            dumps.extend(dump_paths)
        raise OutseeImageError(
            "outsee video: не удалось включить Relax (Безлимит)",
            context={"where": where, "want_on": want_on},
            dumps=dump_paths,
        )

    async def _scan_generate_click_targets(self, page: Page) -> list[dict[str, Any]]:
        try:
            raw = await page.evaluate(
                """() => {
                    const keys = ['генерир', 'generate', 'создать', 'run'];
                    const out = [];
                    for (const el of document.querySelectorAll(
                        'button, [role="button"], a'
                    )) {
                        const text = (el.innerText || el.textContent || '')
                            .trim();
                        const low = text.toLowerCase();
                        if (!keys.some(k => low.includes(k))) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 36 || r.height < 18) continue;
                        const cs = getComputedStyle(el);
                        if (cs.display === 'none' || cs.visibility === 'hidden') {
                            continue;
                        }
                        if (cs.pointerEvents === 'none') continue;
                        const op = parseFloat(cs.opacity || '1');
                        if (op < 0.15) continue;
                        out.push({
                            text: text.slice(0, 72),
                            disabled: !!el.disabled
                                || el.getAttribute('aria-disabled') === 'true',
                            cx: Math.round(r.x + r.width / 2),
                            cy: Math.round(r.y + r.height / 2),
                            area: Math.round(r.width * r.height),
                        });
                    }
                    out.sort((a, b) => b.area - a.area);
                    return out;
                }"""
            )
            return list(raw or [])
        except Exception:  # noqa: BLE001
            return []

    async def _trigger_generate_video(
        self,
        page: Page,
        *,
        input_sel: str | None,
        project_id: int | None = None,
        dumps: list[Path] | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        """Запуск генерации veo — несколько механик, пока не пошла генерация.

        Если генерация уже идёт (кнопка disabled) — ждём до 10 мин
        вместо ошибки.
        """
        from app.services.step_cancel import (
            abort_if_cancelled,
            await_with_cancel,
            sleep_cancellable,
        )

        abort_if_cancelled(project_id)
        strategies_tried: list[str] = []

        # 0) Если генерация уже идёт — ждём её завершения (до 600с).
        if await self._generation_started(page):
            logger.info(
                "outsee.generate_video: генерация уже идёт — ожидаю "
                "завершения (до 600с)…"
            )
            strategies_tried.append("wait_existing")
            for _w in range(600):
                abort_if_cancelled(project_id)
                await sleep_cancellable(1.0, project_id)
                if not await self._generation_started(page):
                    logger.info(
                        "outsee.generate_video: предыдущая генерация "
                        "завершилась, продолжаю"
                    )
                    break
            else:
                logger.warning(
                    "outsee.generate_video: предыдущая генерация не "
                    "завершилась за 600с"
                )

        async def _started() -> bool:
            await sleep_cancellable(0.8, project_id)
            return await self._generation_started(page)

        # A) Скан DOM → клик по всем найденным кнопкам (до 10).
        # Три типа клика на каждую: CDP, mouse, JS dispatchEvent.
        targets = await self._scan_generate_click_targets(page)
        gen_first = [
            t
            for t in targets
            if "генерир" in str(t.get("text") or "").lower()
            or "generate" in str(t.get("text") or "").lower()
        ]
        ordered = gen_first + [t for t in targets if t not in gen_first]
        logger.info(
            "outsee.generate_video: scan {} кнопок (генерир-first: {})",
            len(targets),
            len(gen_first),
        )
        for idx, t in enumerate(ordered[:10]):
            if t.get("disabled"):
                continue
            cx, cy = float(t["cx"]), float(t["cy"])
            text = str(t.get("text") or "")[:40]
            abort_if_cancelled(project_id)

            # CDP click
            await _cdp_dispatch_click(
                page, cx, cy, project_id=project_id
            )
            strategies_tried.append(f"cdp#{idx}")
            logger.info(
                "outsee.generate_video: CDP click #{} ({:.0f},{:.0f}) {!r}",
                idx, cx, cy, text,
            )
            if await _started():
                return

            # Physical mouse click
            await _viewport_mouse_click(
                page, cx, cy, project_id=project_id, label=f"gen#{idx}"
            )
            strategies_tried.append(f"mouse#{idx}")
            if await _started():
                return

            # JS dispatchEvent click
            try:
                await page.evaluate(
                    """([x, y]) => {
                        const el = document.elementFromPoint(x, y);
                        if (!el) return false;
                        const btn = el.closest(
                            'button, [role="button"], a'
                        ) || el;
                        for (const type of [
                            'pointerdown', 'mousedown', 'mouseup',
                            'pointerup', 'click'
                        ]) {
                            btn.dispatchEvent(new MouseEvent(type, {
                                bubbles: true, cancelable: true,
                                view: window, clientX: x, clientY: y,
                            }));
                        }
                        return true;
                    }""",
                    [cx, cy],
                )
                strategies_tried.append(f"js#{idx}")
                if await _started():
                    logger.info(
                        "outsee.generate_video: старт по JS #{}", idx
                    )
                    return
            except Exception:  # noqa: BLE001
                pass

        # B) Горячие клавиши.
        if input_sel:
            for keys, name in (
                ("Control+Enter", "ctrl+enter"),
                ("Enter", "enter"),
            ):
                abort_if_cancelled(project_id)
                try:
                    loc = page.locator(input_sel).first
                    await await_with_cancel(loc.focus(), project_id)
                    await sleep_cancellable(0.15, project_id)
                    await await_with_cancel(
                        page.keyboard.press(keys), project_id
                    )
                    strategies_tried.append(name)
                    logger.info(
                        "outsee.generate_video: попытка {}", name
                    )
                    if await _started():
                        logger.info(
                            "outsee.generate_video: старт по {}", name
                        )
                        return
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "outsee.generate_video: {} failed: {}", name, e
                    )

        # C) Playwright get_by_role / селекторы (force).
        for label, click_fn in (
            (
                "role_генерировать",
                lambda: page.get_by_role(
                    "button",
                    name=re.compile(r"генерир|generate|создать", re.I),
                ).first.click(force=True, timeout=5_000),
            ),
            (
                "selector",
                lambda: self._click_generate_button(
                    page,
                    where="generate_video",
                    project_id=project_id,
                    dumps=dumps,
                    context=context,
                    physical_only=False,
                ),
            ),
        ):
            abort_if_cancelled(project_id)
            try:
                if label == "selector":
                    await click_fn()
                else:
                    await await_with_cancel(click_fn(), project_id)
                strategies_tried.append(label)
                if await _started():
                    logger.info(
                        "outsee.generate_video: старт по {}", label
                    )
                    return
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "outsee.generate_video: {} failed: {}",
                    label,
                    type(e).__name__,
                )

        # D) physical-only по селекторам (последний шанс).
        try:
            await self._click_generate_button(
                page,
                where="generate_video",
                project_id=project_id,
                dumps=dumps,
                context=context,
                physical_only=True,
            )
            strategies_tried.append("physical_selectors")
            if await _started():
                return
        except OutseeImageError:
            pass

        # E) Повторный скан после задержки — outsee мог
        # перерисовать DOM после вставки промта.
        logger.info(
            "outsee.generate_video: все стратегии провалились — "
            "ждём 3с и пробую повторный скан"
        )
        await sleep_cancellable(3.0, project_id)
        targets2 = await self._scan_generate_click_targets(page)
        for idx2, t2 in enumerate(targets2[:10]):
            if t2.get("disabled"):
                continue
            cx2, cy2 = float(t2["cx"]), float(t2["cy"])
            text2 = str(t2.get("text") or "")[:40]
            abort_if_cancelled(project_id)
            await _cdp_dispatch_click(
                page, cx2, cy2, project_id=project_id
            )
            strategies_tried.append(f"rescan_cdp#{idx2}")
            logger.info(
                "outsee.generate_video: rescan CDP #{} ({:.0f},{:.0f}) {!r}",
                idx2, cx2, cy2, text2,
            )
            if await _started():
                return
            await _viewport_mouse_click(
                page, cx2, cy2, project_id=project_id,
                label=f"rescan#{idx2}",
            )
            strategies_tried.append(f"rescan_mouse#{idx2}")
            if await _started():
                return

        dump_paths: list[Path] = []
        h, p = await _dump_page(page, "video_generate_all_failed")
        for x in (h, p):
            if x:
                dump_paths.append(x)
        if dumps is not None:
            dumps.extend(dump_paths)
        raise OutseeImageError(
            "outsee video: не удалось запустить Generate "
            "(все механики: клавиши, CDP, мышь, JS, role, селекторы, rescan)",
            context={
                **(context or {}),
                "strategies": strategies_tried,
                "targets": (targets + targets2)[:10],
            },
            dumps=dump_paths,
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
          - не были в baseline до старта генерации (сравнение по host+path,
            без `?query` — иначе перевыпуск AWS-подписи на каждом ререндере
            фолсли помечает галерейные thumb'ы как «новые»),
          - уже полностью загружены (img.complete && naturalWidth>0),
          - имеют natural-размер ≥200×200 (отсекает иконки/аватары).
        Список упорядочен в порядке появления в DOM (первый элемент —
        самая верхняя карточка в галерее outsee, обычно самая свежая —
        outsee рендерит результаты сверху-вниз новейшими-первыми).

        ВАЖНО: `baseline_srcs` должен быть множеством НОРМАЛИЗОВАННЫХ
        URL (без query). Колл-сайт обязан использовать `_strip_url_query`
        при формировании baseline."""
        baseline_list = list(baseline_srcs)
        try:
            res = await page.evaluate(
                """(baseline) => {
                    const skip = new Set(baseline);
                    const stripQ = (u) => {
                        if (!u) return '';
                        const hash = u.indexOf('#');
                        if (hash >= 0) u = u.substring(0, hash);
                        const q = u.indexOf('?');
                        if (q >= 0) u = u.substring(0, q);
                        return u;
                    };
                    const out = [];
                    for (const img of document.querySelectorAll('img')) {
                        if (!img.src) continue;
                        const stable = stripQ(img.src);
                        if (skip.has(stable)) continue;
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

    async def _completed_new_videos(
        self, page: Page, baseline_urls: set[str]
    ) -> list[str]:
        """Новые URL роликов в DOM (вне baseline, readyState≥2)."""
        baseline_list = list(baseline_urls)
        try:
            res = await page.evaluate(
                """(baseline) => {
                    const skip = new Set(baseline);
                    const stripQ = (u) => {
                        if (!u) return '';
                        const hash = u.indexOf('#');
                        if (hash >= 0) u = u.substring(0, hash);
                        const q = u.indexOf('?');
                        if (q >= 0) u = u.substring(0, q);
                        return u;
                    };
                    const seen = new Set();
                    const out = [];
                    const push = (u) => {
                        if (!u || u.startsWith('data:')) return;
                        const stable = stripQ(u);
                        if (skip.has(stable) || seen.has(stable)) return;
                        seen.add(stable);
                        out.push(u);
                    };
                    for (const v of document.querySelectorAll('video')) {
                        let src = v.currentSrc || v.src || '';
                        if (!src) {
                            const s = v.querySelector('source');
                            if (s && s.src) src = s.src;
                        }
                        if (!src) continue;
                        if (v.readyState < 2) continue;
                        push(src);
                    }
                    document.querySelectorAll(
                        "a[download], a[href*='.mp4']"
                    ).forEach(a => { if (a.href) push(a.href); });
                    return out;
                }""",
                baseline_list,
            )
            return [u for u in (res or []) if _video_url_looks_like_result(u)]
        except Exception:  # noqa: BLE001
            return []

    async def _find_img_by_prompt_id(
        self,
        page: Page,
        id_token: str,
        *,
        max_levels: int = 20,
    ) -> str | None:
        """Ищет в DOM «карточку», в которой видимый текст содержит `id_token`,
        и возвращает src ближайшей к этому тексту `<img>`, удовлетворяющей
        проверкам (загружена, naturalWidth >= 200).

        Пытается несколько токенов в порядке убывания строгости:
          1) полный `[ID: P1-HERO1-V1-xxxxxxxx]` (как в промте);
          2) то же без квадратных скобок и `ID:` — `P1-HERO1-V1-xxxxxxxx`
             (на случай если outsee экранирует `[` `]` или вставляет
             zero-width-чары между ними);
          3) только 8-hex-tail (`xxxxxxxx`) — он глобально уникальный
             (uuid.hex[:8]), и точно не подменится outsee'ем.

        Используется для строгой верификации: outsee показывает в карточке
        результата начало промта, и так как мы кладём `[ID: P1-HERO1-V1-…]`
        первой строкой каждого промта, у НАС всегда есть однозначный
        идентификатор для сопоставления «картинка ↔ моя генерация».
        Это отсекает любые старые/чужие фото из истории outsee.
        """
        tokens = _prompt_id_search_tokens(id_token)

        js = """
        ([tokens, maxLevels]) => {
            const hasToken = (el, idToken) => {
                if (!el) return false;
                const t = (el.innerText || el.textContent || '');
                if (t.includes(idToken)) return true;
                const tag = el.tagName && el.tagName.toLowerCase();
                if (tag === 'textarea' || tag === 'input') {
                    const v = el.value || '';
                    if (v.includes(idToken)) return true;
                }
                // data-* attributes and title might hold prompt text
                try {
                    for (const attr of el.attributes || []) {
                        if ((attr.name.startsWith('data-') || attr.name === 'title' || attr.name === 'aria-label')
                            && attr.value && attr.value.includes(idToken)) return true;
                    }
                } catch (_) {}
                return false;
            };

            // Strategy A: targeted card-like containers first (faster than '*')
            const cardSelectors = [
                'article', '[class*="card"]', '[class*="Card"]',
                '[class*="result"]', '[class*="Result"]',
                '[class*="gallery"]', '[class*="Gallery"]',
                '[data-testid]', '[role="listitem"]', '[role="article"]',
                'li', 'section > div', 'main > div > div',
            ];
            for (const idToken of tokens) {
                for (const sel of cardSelectors) {
                    try {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            if (!hasToken(el, idToken)) continue;
                            let cur = el;
                            for (let i = 0; i < maxLevels && cur; i++) {
                                const imgs = cur.querySelectorAll('img');
                                for (const img of imgs) {
                                    if (!img.src || img.src.startsWith('data:')) continue;
                                    if (!img.complete || !img.naturalWidth || img.naturalWidth < 200) continue;
                                    return img.src;
                                }
                                cur = cur.parentElement;
                            }
                        }
                    } catch (_) {}
                }
            }

            // Strategy B: full DOM scan (original logic, catches anything Strategy A missed)
            for (const idToken of tokens) {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (!el || !el.children) continue;
                    if (el === document.body || el === document.documentElement) continue;
                    if (!hasToken(el, idToken)) continue;
                    // Descend to smallest containing element
                    let smallest = el;
                    let childHas = false;
                    for (const child of el.children) {
                        if (hasToken(child, idToken)) { childHas = true; break; }
                    }
                    if (childHas) {
                        // A child also has the token — skip this level but
                        // DO NOT skip the whole element; the child will be
                        // visited on its own in the outer loop.
                        continue;
                    }
                    // Also check hidden textarea/input descendants
                    const deepInputs = el.querySelectorAll('textarea, input');
                    let deepMatch = false;
                    for (const di of deepInputs) {
                        if (di === el) continue;
                        const v = di.value || '';
                        if (v.includes(idToken)) { deepMatch = true; break; }
                    }
                    if (deepMatch) continue;
                    // Walk up to find an ancestor with <img>
                    let cur = smallest;
                    for (let i = 0; i < maxLevels && cur; i++) {
                        const imgs = cur.querySelectorAll('img');
                        for (const img of imgs) {
                            if (!img.src) continue;
                            if (img.src.startsWith('data:')) continue;
                            if (!img.complete) continue;
                            if (!img.naturalWidth || img.naturalWidth < 200) continue;
                            return img.src;
                        }
                        cur = cur.parentElement;
                    }
                }
            }

            // Strategy C: zero-width-char-tolerant search (outsee sometimes
            // inserts invisible chars between brackets / after ID:)
            // Excludes composer text to avoid matching our own input.
            for (const idToken of tokens) {
                if (idToken.length < 6) continue;
                const stripped = idToken.replace(/[\\[\\]]/g, '').replace('ID:', '').trim();
                if (!stripped || stripped.length < 6) continue;
                const composerSels = [
                    "textarea[placeholder*='prompt' i]",
                    "textarea[placeholder*='промпт' i]",
                    "textarea[name='prompt']",
                    "textarea[data-testid='prompt']",
                ];
                const composerEls = new Set();
                for (const sel of composerSels) {
                    try { for (const el of document.querySelectorAll(sel)) composerEls.add(el); } catch (_) {}
                }
                const bodyText = (document.body.innerText || '') + '\\n';
                const textareas = document.querySelectorAll('textarea, input');
                let fullText = bodyText;
                for (const ta of textareas) {
                    if (composerEls.has(ta)) continue;
                    if (ta.value) fullText += ta.value + '\\n';
                }
                const cleanText = fullText.replace(/[\\u200B\\u200C\\u200D\\uFEFF\\u00AD]/g, '');
                if (!cleanText.includes(stripped)) continue;
                // Token found with zero-width chars stripped — now locate the
                // containing card by walking all elements again
                const all2 = document.querySelectorAll('*');
                for (const el of all2) {
                    if (!el || el === document.body || el === document.documentElement) continue;
                    const t = ((el.innerText || el.textContent || '') +
                               (el.value || '')).replace(/[\\u200B\\u200C\\u200D\\uFEFF\\u00AD]/g, '');
                    if (!t.includes(stripped)) continue;
                    let childHas2 = false;
                    for (const ch of (el.children || [])) {
                        const ct = ((ch.innerText || ch.textContent || '') +
                                    (ch.value || '')).replace(/[\\u200B\\u200C\\u200D\\uFEFF\\u00AD]/g, '');
                        if (ct.includes(stripped)) { childHas2 = true; break; }
                    }
                    if (childHas2) continue;
                    let cur = el;
                    for (let i = 0; i < maxLevels && cur; i++) {
                        const imgs = cur.querySelectorAll('img');
                        for (const img of imgs) {
                            if (!img.src || img.src.startsWith('data:')) continue;
                            if (!img.complete || !img.naturalWidth || img.naturalWidth < 200) continue;
                            return img.src;
                        }
                        cur = cur.parentElement;
                    }
                }
            }
            return null;
        }
        """
        try:
            res = await page.evaluate(js, [tokens, max_levels])
            if isinstance(res, str) and res:
                return res
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_find_img_by_prompt_id: ошибка JS-поиска: {}", e
            )

        # Strategy D: Playwright locator-based search using get_by_text.
        # This catches cases where JS evaluate misses elements due to
        # iframe boundaries, shadow DOM, or Playwright's stricter text
        # matching semantics.
        for tok in tokens:
            if not tok or len(tok) < 6:
                continue
            try:
                text_loc = page.get_by_text(tok, exact=False).first
                if await text_loc.count() == 0:
                    continue
                ancestor = text_loc
                for _lvl in range(max_levels):
                    img_loc = ancestor.locator("img").first
                    if await img_loc.count() > 0:
                        src = await img_loc.get_attribute("src")
                        if src and not src.startswith("data:"):
                            try:
                                loaded = await img_loc.evaluate(
                                    "el => el.complete && (el.naturalWidth || 0) >= 200"
                                )
                                if loaded:
                                    logger.info(
                                        "_find_img_by_prompt_id: Strategy D "
                                        "(Playwright get_by_text) нашла: {}",
                                        src[:120],
                                    )
                                    return src
                            except Exception:  # noqa: BLE001
                                pass
                    ancestor = ancestor.locator("xpath=..")
                    if await ancestor.count() == 0:
                        break
            except Exception:  # noqa: BLE001
                continue

        return None

    async def _count_id_tokens_in_page(
        self, page: Page, tokens: list[str]
    ) -> dict[str, int]:
        """Возвращает карту {token: количество_вхождений} на странице.

        Сканирует:
          1) `body.innerText` — рендеренный текст;
          2) `<textarea>.value` и `<input>.value` — значения форм,
             которые **не** попадают в innerText. Outsee рендерит
             промт в правой панели через <textarea readonly>, и без
             этой проверки счётчик не растёт после клика → ложное
             «чужая картинка» в `_verify_img_by_clicking`.

        Используется для дифференциальной проверки клика: если outsee
        уже показывает наш `[ID: ...]` где-то на странице (например, в
        панели «генерация в процессе» или в карточке композера) ДО
        клика — простой `includes`-чек даст ложноположительный ответ
        для любой кликнутой картинки. Считаем количество вхождений и
        смотрим РОСТ после клика — рост означает «открылась наша
        карточка из gallery».
        """
        js = """
        ([toks]) => {
            const body = document.body;
            let text = (body && (body.innerText || body.textContent)) || '';
            // Добавляем значения <textarea>/<input> — они не попадают
            // в innerText, но outsee рендерит туда полный промт.
            for (const el of document.querySelectorAll(
                'textarea, input[type=text], input:not([type])'
            )) {
                const v = el && el.value;
                if (v) text += '\\n' + v;
            }
            const result = {};
            for (const tok of toks) {
                if (!tok) { result[tok] = 0; continue; }
                // text.split(tok).length - 1 = количество вхождений
                result[tok] = text.split(tok).length - 1;
            }
            return result;
        }
        """
        try:
            res = await page.evaluate(js, [tokens])
            if isinstance(res, dict):
                return {t: int(res.get(t, 0) or 0) for t in tokens}
            return dict.fromkeys(tokens, 0)
        except Exception:  # noqa: BLE001
            return dict.fromkeys(tokens, 0)

    async def _verify_img_by_clicking(
        self, page: Page, target_src: str, id_token: str
    ) -> bool:
        """Кликает в DOM на `<img>` с src=`target_src`, ждёт появления
        панели «ПРОМПТ» (outsee рисует её только по клику на картинку),
        и проверяет, что в видимом тексте этой панели присутствует
        `id_token` (или его 8-hex-tail).

        Дифференциальная проверка: outsee уже МОЖЕТ показывать наш
        `[ID: ...]` где-то (например, в индикаторе «генерация в процессе»
        с прикреплённым к ней нашим же текущим промтом). Поэтому мы:
          1. Снимаем количество вхождений токенов в body.innerText ДО клика.
          2. Кликаем картинку.
          3. Ждём что количество УВЕЛИЧИТСЯ — это значит что после клика
             в DOM добавилась карточка с этим же токеном (наша картинка
             из gallery).
          4. Если количество не изменилось — клик не открыл нашу карточку,
             это чужая картинка → False.

        Возвращает True/False. После проверки закрываем панель Esc'ом —
        чтобы следующая итерация ждала корректное состояние.
        """
        # Извлекаем 8-hex-tail для liberal-match'а.
        tokens: list[str] = [id_token]
        m = re.search(r"\[ID:\s*([A-Za-z0-9_-]+)\s*\]", id_token)
        if m:
            tokens.append(m.group(1))
        m2 = re.search(r"-([0-9a-fA-F]{8})\]?$", id_token)
        if m2:
            tokens.append(m2.group(1))

        try:
            # 1. Снапшот вхождений ДО клика.
            pre_count = await self._count_id_tokens_in_page(page, tokens)
            pre_total = sum(pre_count.values())

            # 2. Клик по img с нужным src — через JS, потому что обычный
            # locator может не найти элемент если он внутри сложной
            # модалки/canvas-обёртки.
            clicked = await page.evaluate(
                """(targetSrc) => {
                    for (const img of document.querySelectorAll('img')) {
                        if (img.src === targetSrc) {
                            img.scrollIntoView({block:'center'});
                            // Кликабельный родитель — обычно <button>
                            // или <a>, но если нет, кликаем сам img.
                            let target = img;
                            for (let i = 0; i < 4; i++) {
                                if (!target.parentElement) break;
                                const tag = target.tagName?.toLowerCase();
                                if (tag === 'button' || tag === 'a') break;
                                target = target.parentElement;
                            }
                            target.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                target_src,
            )
            if not clicked:
                logger.warning(
                    "_verify_img_by_clicking: не нашёл <img src='{}'> в DOM",
                    target_src[:100],
                )
                return False

            # 3. Ждём пока правая панель отрендерит prompt-текст.
            # Дифференциальная проверка: ждём чтобы количество вхождений
            # хотя бы одного токена УВЕЛИЧИЛОСЬ после клика — это значит
            # что после клика в DOM добавилась карточка с нашим ID.
            # Polling до 5 секунд.
            for _ in range(10):
                await asyncio.sleep(0.5)
                cur_count = await self._count_id_tokens_in_page(page, tokens)
                cur_total = sum(cur_count.values())
                if cur_total > pre_total:
                    logger.info(
                        "_verify_img_by_clicking: token count вырос после "
                        "клика ({} -> {}, pre={}, cur={}), это НАША картинка",
                        pre_total, cur_total, pre_count, cur_count,
                    )
                    return True

            # 4. Количество не выросло — клик не открыл нашу карточку.
            logger.warning(
                "_verify_img_by_clicking: token count НЕ вырос после клика "
                "(pre_total={}, pre={}). Outsee либо показывает наш токен "
                "только в композере (поэтому он попадает в body всегда), "
                "либо это чужая картинка. Считаем что чужая. Diag: {}",
                pre_total, pre_count,
                await self._diag_id_in_page(page, id_token),
            )
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_verify_img_by_clicking: исключение: {}", e
            )
            return False
        finally:
            # Закрываем панель Escape'ом, чтобы следующая итерация
            # ожидания не зависела от текущего состояния модалки.
            try:
                await page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass

    async def _diag_id_in_page(
        self, page: Page, id_token: str
    ) -> dict[str, Any]:
        """Диагностика: сообщает, встречается ли `id_token` (или его
        составляющие) где-либо в DOM-тексте страницы. Используется,
        когда `_find_img_by_prompt_id` не нашёл совпадения, чтобы понять
        — outsee вообще не показывает наш ID, или показывает, но в
        форме, которую наш JS не находит.
        """
        tokens: list[str] = [id_token]
        m = re.search(r"\[ID:\s*([A-Za-z0-9_-]+)\s*\]", id_token)
        if m:
            tokens.append(m.group(1))
        m2 = re.search(r"-([0-9a-fA-F]{8})\]?$", id_token)
        if m2:
            tokens.append(m2.group(1))

        js = """
        ([tokens]) => {
            const body = document.body;
            let text = (body && (body.innerText || body.textContent)) || '';
            for (const el of document.querySelectorAll(
                'textarea, input[type=text], input:not([type])'
            )) {
                const v = el && el.value;
                if (v) text += '\\n' + v;
            }
            const result = {};
            for (const tok of tokens) {
                result[tok] = text.includes(tok);
            }
            // Также вернём количество <img> в DOM с непустым src
            const imgs = document.querySelectorAll('img');
            let total = 0, complete = 0;
            for (const img of imgs) {
                if (!img.src || img.src.startsWith('data:')) continue;
                total++;
                if (img.complete && img.naturalWidth >= 200) complete++;
            }
            result['__imgs_total'] = total;
            result['__imgs_complete'] = complete;
            return result;
        }
        """
        try:
            res = await page.evaluate(js, [tokens])
            if isinstance(res, dict):
                return res
            return {}
        except Exception:  # noqa: BLE001
            return {}

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
        prompt_id_prefix: str | None = None,
        project_id: int | None = None,
    ) -> str:
        """Жёсткое ожидание свежей картинки.

        ПРИОРИТЕТ 0 (если передан `prompt_id_prefix`, например
        `[ID: P1-HERO1-V1-349303db]`): ищем в DOM карточку, у которой
        видимый текст содержит этот токен, и возвращаем `<img>` из неё.
        Это самая строгая проверка — она исключает все картинки из
        history-галереи outsee и любые «чужие» резалт-карточки.

        Если `prompt_id_prefix` не передан — работают старые правила:

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
        # Кандидат от «старой» логики (baseline + net_events). Когда
        # появляется, бот кликает по нему в браузере чтобы открыть
        # outsee'вскую правую панель «Промпт» и проверить [ID: ...].
        # Если ID совпал — это наша картинка, возвращаем; если нет —
        # это чужая из gallery, добавляем в rejected_candidates и ждём
        # дальше.
        fallback_candidate: str | None = None
        fallback_source: str | None = None  # "result_block" | "new_dom"
        # URL'ы, для которых клик-верификация уже была проведена и
        # ID не совпал. Чтобы не кликать одну и ту же чужую картинку
        # снова и снова.
        # Храним НОРМАЛИЗОВАННЫЕ URL (без query), потому что re-sign
        # на каждом ререндере меняет raw URL, и «та же» чужая
        # картинка в следующем итерейшене click-verify выглядела бы как
        # другой URL.
        rejected_candidates: set[str] = set()

        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
            now = asyncio.get_event_loop().time()
            elapsed = now - start

            # 0) Fail-fast: ошибка генерации / модерация (до ожидания img).
            if elapsed >= 1.5:
                failure = await self._detect_outsee_failure(page)
                if failure:
                    ftext = failure["text"]
                    in_result = bool(failure.get("in_result"))
                    gen_idle = await self._generate_button_enabled(page)
                    # Не считаем ошибку «новой» только из-за gen_idle:
                    # плашка из сайдбара/композера (in_result=False) иначе
                    # рвёт успешную генерацию и уводит в retry без download-v3.
                    is_new = (
                        in_result
                        or not pre_rejected_text
                        or ftext != pre_rejected_text
                    )
                    if is_new:
                        logger.info(
                            "_wait_image_url_strict: ошибка outsee за "
                            "{:.0f} сек (in_result={}, gen_idle={}, "
                            "kind={}): {}",
                            elapsed,
                            in_result,
                            gen_idle,
                            _outsee_failure_kind(ftext),
                            ftext[:120],
                        )
                        _raise_outsee_failure(
                            text=ftext,
                            gen_id=gen_id,
                            elapsed=elapsed,
                            in_result=in_result,
                        )

            # 1) ВЫСШИЙ приоритет — поиск картинки по `prompt_id_prefix`.
            # Outsee рендерит в карточке результата начало промта, и наш
            # `[ID: P1-HERO1-V1-…]` всегда стоит первой строкой. Если в
            # DOM появилась карточка с НАШИМ ID — берём её картинку,
            # независимо от baseline и порядка. Это полностью отсекает
            # старые/чужие фото из истории outsee.
            if prompt_id_prefix:
                by_id = await self._find_img_by_prompt_id(
                    page, prompt_id_prefix
                )
                if by_id:
                    by_id_norm = _strip_url_query(by_id)
                    fresh_ok = (
                        by_id_norm != baseline_result_img
                        and by_id_norm not in baseline_all_srcs
                        and not any(
                            m in by_id.lower() for m in _UI_ASSET_MARKERS
                        )
                        and not any(
                            m in by_id.lower() for m in _INPUT_REF_MARKERS
                        )
                    )
                    if fresh_ok:
                        # Extra verify: ensure the image is fully loaded
                        loaded = await self._img_is_loaded(page, by_id)
                        if loaded:
                            logger.info(
                                "_wait_image_url_strict: matched by prompt_id "
                                "{} за {:.0f} сек (loaded=True): {}",
                                prompt_id_prefix,
                                elapsed,
                                by_id[:140],
                            )
                            return by_id
                        else:
                            logger.info(
                                "_wait_image_url_strict: ID match found but "
                                "img not fully loaded yet, waiting...",
                            )

            # 1) Параллельно — отслеживаем кандидата по «старой» логике
            # (baseline + net_events). Сохраняем последнего подходящего
            # в `fallback_candidate`, но НЕ возвращаем сразу: даём шанс
            # ID-верификации найти именно НАШУ карточку. Если ID-поиск
            # за весь timeout ничего не найдёт, возьмём этот кандидат
            # как safety-net (с WARNING в лог).
            current = await self._result_img_src(page)
            last_seen_result = current
            current_norm = _strip_url_query(current) if current else ""
            if (
                current
                and current_norm != baseline_result_img
                and not current.endswith("/placeholder.svg")
                and "data:image" not in current
                and not any(
                    m in current.lower() for m in _INPUT_REF_MARKERS
                )
                and not any(
                    m in current.lower() for m in _UI_ASSET_MARKERS
                )
                and current_norm not in baseline_all_srcs
            ):
                if await self._img_is_loaded(page, current):
                    # Пока net_events пуст — доверяем DOM (новый src вне
                    # baseline). Иначе Outsee может отдать картинку без
                    # image/* response ≥50KB в listener.
                    if (not net_events) or _url_is_fresh(current, net_events):
                        if not prompt_id_prefix:
                            # Без ID-верификации — возвращаем сразу
                            # (старое поведение).
                            logger.info(
                                "_wait_image_url_strict: «Результат генерации» "
                                "за {:.0f} сек: {}",
                                elapsed,
                                current[:140],
                            )
                            return current
                        else:
                            # С ID-верификацией — только запоминаем,
                            # если ещё не отвергали этот URL (по норм.).
                            if (
                                _strip_url_query(current)
                                not in rejected_candidates
                            ):
                                fallback_candidate = current
                                fallback_source = "result_block"

            new_srcs = await self._completed_new_imgs(page, baseline_all_srcs)
            if new_srcs:
                clean = [
                    u
                    for u in new_srcs
                    if not any(m in u.lower() for m in _UI_ASSET_MARKERS)
                    and not any(m in u.lower() for m in _INPUT_REF_MARKERS)
                ]
                if net_events:
                    clean = [u for u in clean if _url_is_fresh(u, net_events)]
                # Исключаем уже отвергнутых при ID-верификации (сравнение
                # по нормализованным URL'ам — см. _strip_url_query).
                if prompt_id_prefix:
                    clean = [
                        u for u in clean
                        if _strip_url_query(u) not in rejected_candidates
                    ]
                if clean:
                    # ПИКАЕМ FIRST (не last): outsee рендерит результаты
                    # сверху-вниз новейшими-первыми, поэтому первый элемент DOM
                    # в `new_srcs` — самая свежая карточка. Старый код
                    # брал clean[-1] — last — и в ситуации «все галерейные
                    # thumb'ы выглядят new из-за re-sign URL» это приводило
                    # к скачиванию старой картинки. Теперь baseline нормализован
                    # (без query) + берём first.
                    chosen = clean[0]
                    if not prompt_id_prefix:
                        logger.info(
                            "_wait_image_url_strict: новая <img> в DOM за "
                            "{:.0f} сек: {} (всего новых: {})",
                            elapsed,
                            chosen[:140],
                            len(clean),
                        )
                        return chosen
                    else:
                        fallback_candidate = chosen
                        fallback_source = "new_dom"
                        if len(clean) > 1:
                            logger.info(
                                "_wait_image_url_strict: new_srcs={} (>1) — "
                                "беру первый по DOM (новейший в outsee), "
                                "проверю click/net_events: {}",
                                len(clean), chosen[:120],
                            )
                        # ID search failed but new images exist — run
                        # diagnostics to understand if outsee shows our
                        # ID in an unreadable form vs not at all.
                        if prompt_id_prefix and not by_id:
                            diag = await self._diag_id_in_page(
                                page, prompt_id_prefix
                            )
                            id_in_page = any(
                                v for k, v in diag.items()
                                if not k.startswith("__")
                            )
                            if id_in_page:
                                logger.info(
                                    "_wait_image_url_strict: ID обнаружен "
                                    "в тексте страницы, но _find_img_by_prompt_id "
                                    "не нашёл карточку. diag={}",
                                    diag,
                                )

            # 2.7) Click-verify disambiguation: when we have multiple
            # new_srcs and ID search failed, try clicking each candidate
            # image and check _gallery_detail_panel_has_id (composer-safe).
            # This is the revived _verify_img_by_clicking logic but using
            # the panel-only check instead of full-page differential count.
            if (
                prompt_id_prefix
                and fallback_candidate
                and new_srcs
                and len(clean) > 1
                and elapsed >= 4.0
            ):
                for cand_url in clean[:5]:
                    cand_norm = _strip_url_query(cand_url)
                    if cand_norm in rejected_candidates:
                        continue
                    try:
                        clicked = await page.evaluate(
                            """(targetSrc) => {
                                for (const img of document.querySelectorAll('img')) {
                                    if (img.src === targetSrc) {
                                        img.scrollIntoView({block:'center'});
                                        img.click();
                                        return true;
                                    }
                                }
                                return false;
                            }""",
                            cand_url,
                        )
                        if not clicked:
                            continue
                        await asyncio.sleep(0.6)
                        if await _gallery_detail_panel_has_id(
                            page, prompt_id_prefix
                        ):
                            logger.info(
                                "_wait_image_url_strict: click-verify "
                                "подтвердил {} как НАШУ картинку ✓",
                                cand_url[:100],
                            )
                            fallback_candidate = cand_url
                            fallback_source = "click_verified"
                            break
                        else:
                            rejected_candidates.add(cand_norm)
                            with contextlib.suppress(Exception):
                                await page.keyboard.press("Escape")
                            await asyncio.sleep(0.2)
                    except Exception:  # noqa: BLE001
                        pass
            _MIN_SEC_BEFORE_DOWNLOAD_HANDOFF = 6.0
            if (
                prompt_id_prefix
                and fallback_candidate is not None
                and _strip_url_query(fallback_candidate)
                not in rejected_candidates
            ):
                gen_idle = await self._generate_button_enabled(page)
                if gen_idle and elapsed >= _MIN_SEC_BEFORE_DOWNLOAD_HANDOFF:
                    # Before handing off a non-ID-verified URL, wait for
                    # gallery thumbs and attempt one more ID search — this
                    # can catch cards that appeared after gallery lazy-load.
                    n_thumbs = await _wait_gallery_thumbs(
                        page, min_count=1, timeout_s=5.0,
                        project_id=project_id,
                    )
                    if n_thumbs >= 1:
                        by_id_final = await self._find_img_by_prompt_id(
                            page, prompt_id_prefix
                        )
                        if by_id_final:
                            by_id_final_norm = _strip_url_query(by_id_final)
                            if (
                                by_id_final_norm != baseline_result_img
                                and by_id_final_norm not in baseline_all_srcs
                                and not any(
                                    m in by_id_final.lower()
                                    for m in _UI_ASSET_MARKERS
                                )
                                and not any(
                                    m in by_id_final.lower()
                                    for m in _INPUT_REF_MARKERS
                                )
                            ):
                                logger.info(
                                    "_wait_image_url_strict: matched by "
                                    "prompt_id (pre-handoff) {} за {:.0f} сек",
                                    prompt_id_prefix, elapsed,
                                )
                                return by_id_final

                    # Try net_events URL for direct card lookup — the
                    # newest network image URL may correspond to our card
                    # even if DOM search failed.
                    newest = _newest_fresh_url(
                        net_events, baseline_srcs=baseline_all_srcs
                    )
                    if newest and _strip_url_query(newest) not in rejected_candidates:
                        card = await _find_card_by_img_url_click(
                            page, newest, project_id=project_id
                        )
                        if card is not None:
                            logger.info(
                                "_wait_image_url_strict: net_events URL → "
                                "card found via _find_card_by_img_url_click, "
                                "handoff с карточкой: {}",
                                newest[:100],
                            )
                            fallback_candidate = newest
                            fallback_source = "net_card"

                    page_text = await _page_text_excluding_composer(page)
                    id_tokens = _prompt_id_search_tokens(prompt_id_prefix)
                    id_in_non_composer = any(
                        tok in page_text for tok in id_tokens if tok
                    )
                    logger.info(
                        "_wait_image_url_strict: gen завершена (gen_idle), "
                        "переход к download-v3 — перебор 15 картинок "
                        "(source={}, {:.0f} сек, id_in_page={}, url={})",
                        fallback_source,
                        elapsed,
                        id_in_non_composer,
                        fallback_candidate[:120],
                    )
                    return fallback_candidate

            if (
                prompt_id_prefix
                and elapsed >= _MIN_SEC_BEFORE_DOWNLOAD_HANDOFF
                and await self._generate_button_enabled(page)
            ):
                # Check MutationObserver for newly added images
                try:
                    observer_imgs = await page.evaluate(
                        "() => window.__vp_new_imgs || []"
                    )
                    if observer_imgs:
                        obs_clean = [
                            u for u in observer_imgs
                            if _strip_url_query(u) not in baseline_all_srcs
                            and not any(
                                m in u.lower() for m in _UI_ASSET_MARKERS
                            )
                            and not any(
                                m in u.lower() for m in _INPUT_REF_MARKERS
                            )
                            and _strip_url_query(u) not in rejected_candidates
                        ]
                        if obs_clean and not fallback_candidate:
                            fallback_candidate = obs_clean[0]
                            fallback_source = "mutation_observer"
                            logger.info(
                                "_wait_image_url_strict: MutationObserver "
                                "caught {} new img(s), first: {}",
                                len(obs_clean), obs_clean[0][:100],
                            )
                except Exception:  # noqa: BLE001
                    pass

                # Gen is idle — retry ID search after scrolling gallery
                await _scroll_gallery_to_load_all(
                    page, project_id=project_id
                )
                await asyncio.sleep(0.5)
                by_id_retry = await self._find_img_by_prompt_id(
                    page, prompt_id_prefix
                )
                if by_id_retry:
                    by_id_retry_norm = _strip_url_query(by_id_retry)
                    if (
                        by_id_retry_norm != baseline_result_img
                        and by_id_retry_norm not in baseline_all_srcs
                        and not any(
                            m in by_id_retry.lower()
                            for m in _UI_ASSET_MARKERS
                        )
                        and not any(
                            m in by_id_retry.lower()
                            for m in _INPUT_REF_MARKERS
                        )
                    ):
                        logger.info(
                            "_wait_image_url_strict: matched by prompt_id "
                            "(post-scroll retry) {} за {:.0f} сек: {}",
                            prompt_id_prefix,
                            elapsed,
                            by_id_retry[:140],
                        )
                        return by_id_retry

                idle_srcs = await self._completed_new_imgs(
                    page, baseline_all_srcs
                )
                if idle_srcs:
                    idle_clean = [
                        u
                        for u in idle_srcs
                        if not any(
                            m in u.lower() for m in _UI_ASSET_MARKERS
                        )
                        and not any(
                            m in u.lower() for m in _INPUT_REF_MARKERS
                        )
                    ]
                    if net_events:
                        idle_clean = [
                            u for u in idle_clean
                            if _url_is_fresh(u, net_events)
                        ]
                    elif not net_events:
                        pass  # доверяем DOM
                    if idle_clean:
                        logger.info(
                            "_wait_image_url_strict: gen_idle, новые img в "
                            "DOM — handoff download-v3 за {:.0f} сек: {}",
                            elapsed,
                            idle_clean[0][:120],
                        )
                        return idle_clean[0]

            # 3) diagnostic + periodic gallery scroll to load lazy items
            if elapsed - last_log > 15:
                last_log = elapsed
                n_big = len(await self._all_big_imgs(page))
                if prompt_id_prefix:
                    logger.info(
                        "_wait_image_url_strict: ждём... {:.0f} сек, "
                        "result_img={}, big_imgs={}, fallback_candidate={}",
                        elapsed,
                        (current[:80] if current else None),
                        n_big,
                        (fallback_candidate[:80] if fallback_candidate else None),
                    )
                else:
                    logger.info(
                        "_wait_image_url_strict: ждём... {:.0f} сек, "
                        "result_img_src={}, big_imgs_now={} (baseline={})",
                        elapsed,
                        (current[:80] if current else None),
                        n_big,
                        len(baseline_big_imgs),
                    )
                # Scroll gallery periodically to trigger lazy-loading
                # of new items that may have appeared below the fold
                if prompt_id_prefix:
                    await _scroll_gallery_to_load_all(
                        page, project_id=project_id
                    )
                    # Re-check result block after scroll — it may have
                    # updated with a new image after lazy-load.
                    post_scroll_result = await self._result_img_src(page)
                    if post_scroll_result:
                        ps_norm = _strip_url_query(post_scroll_result)
                        if (
                            ps_norm != baseline_result_img
                            and ps_norm not in baseline_all_srcs
                            and ps_norm not in rejected_candidates
                            and not any(
                                m in post_scroll_result.lower()
                                for m in _UI_ASSET_MARKERS
                            )
                            and not any(
                                m in post_scroll_result.lower()
                                for m in _INPUT_REF_MARKERS
                            )
                            and await self._img_is_loaded(
                                page, post_scroll_result
                            )
                        ):
                            if not fallback_candidate:
                                fallback_candidate = post_scroll_result
                                fallback_source = "result_block_post_scroll"

            await sleep_cancellable(1.0, project_id)

        # timeout — все кандидаты были отвергнуты ID-верификацией
        # (или вообще не появились). Падаем с диагностикой.
        # Normalize URLs for accurate diff (avoids re-sign false positives).
        big_now = {_strip_url_query(u) for u in await self._all_big_imgs(page)}
        new_big = big_now - baseline_big_imgs
        all_now_srcs = {_strip_url_query(u) for u in await self._all_img_srcs(page)}
        new_dom = all_now_srcs - baseline_all_srcs
        ctx: dict[str, Any] = {
            "gen_id": gen_id,
            "baseline_result_img": baseline_result_img,
            "last_result_img_src": last_seen_result,
            "new_big_imgs": ", ".join(list(new_big)[:3]) or "—",
            "new_dom_srcs_count": len(new_dom),
            "baseline_big_imgs": len(baseline_big_imgs),
            "rejected_count": len(rejected_candidates),
            "net_events_count": len(net_events) if net_events else 0,
        }
        # Use _newest_fresh_url as last-chance network-based candidate
        newest_net = _newest_fresh_url(
            net_events, baseline_srcs=baseline_all_srcs
        )
        if newest_net:
            ctx["newest_net_url"] = newest_net[:120]

        if prompt_id_prefix:
            ctx["prompt_id_prefix"] = prompt_id_prefix
            ctx["id_diag"] = await self._diag_id_in_page(page, prompt_id_prefix)
            if await self._generate_button_enabled(page):
                handoff_srcs = await self._completed_new_imgs(
                    page, baseline_all_srcs
                )
                if handoff_srcs:
                    chosen = handoff_srcs[0]
                    logger.warning(
                        "_wait_image_url_strict: timeout {:.0f}с, но gen_idle "
                        "и есть новые img — handoff в download-v3: {}",
                        timeout,
                        chosen[:120],
                    )
                    return chosen
                # DOM has no new imgs, but network caught a fresh URL —
                # use it as last resort before raising error.
                if newest_net:
                    logger.warning(
                        "_wait_image_url_strict: timeout {:.0f}с, gen_idle, "
                        "DOM пуст но net_events имеет свежий URL — "
                        "handoff в download-v3: {}",
                        timeout,
                        newest_net[:120],
                    )
                    return newest_net
        raise OutseeImageError(
            f"outsee image: результат не появился за {int(timeout)} сек",
            context=ctx,
        )

    async def _attach_ref_image_robust(
        self,
        page: Page,
        image_path: Path,
        *,
        where: str,
        project_id: int | None = None,
        prefer_first: bool = False,
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
             он не требует видимости. Для image ref — ПОСЛЕДНИЙ input;
             для video start_frame — ПЕРВЫЙ (левый/начальный кадр;
             последний на странице veo — конечный кадр).

        Возвращает True в случае успеха. False — если input вообще не
        нашлся в DOM или set_input_files упал. Свои dump'ы НЕ снимает —
        это решает вызывающий (у него список `dumps`).
        """
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        abort_if_cancelled(project_id)
        # 0) очистка всех input[type=file]
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
            page, FILE_UPLOAD_SELECTORS, timeout_ms=2_000, project_id=project_id
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
                await sleep_cancellable(1.0, project_id)
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
        pick_first = prefer_first or "start_frame" in where
        target = base.first if pick_first else base.last
        slot_label = "first" if pick_first else "last"
        try:
            await target.set_input_files(str(image_path))
            logger.info(
                "outsee.{}: reference {} загружен в скрытый input "
                "(input[type=file] count={}, взят {})",
                where, image_path.name, count, slot_label,
            )
            await sleep_cancellable(1.0, project_id)
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

    async def _generate_button_enabled(self, page: Page) -> bool:
        """True, если кнопка Generate сейчас активна (генерация не идёт)."""
        try:
            sel = await _first_visible(
                page,
                GENERATE_BUTTON_SELECTORS[:4],
                timeout_ms=800,
            )
            if not sel:
                return False
            loc = page.locator(sel).first
            disabled = await loc.get_attribute("disabled")
            aria = await loc.get_attribute("aria-disabled")
            return disabled is None and (aria or "").lower() != "true"
        except Exception:  # noqa: BLE001
            return False

    async def _detect_outsee_failure(self, page: Page) -> dict[str, object] | None:
        """Видимая плашка ошибки outsee: модерация или сбой генерации.

        Сначала ищет в блоке «Результат генерации» (`in_result=True`),
        затем по всей странице. Возвращает `{text, in_result}` или None.
        """
        mod_js = list(_OUTSEE_MODERATION_MARKERS)
        gen_js = list(_OUTSEE_GENERATION_ERROR_MARKERS)
        try:
            raw = await page.evaluate(
                """(markers) => {
                    const moderation = markers.moderation;
                    const generation = markers.generation;
                    const triggers = moderation.concat(generation);

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

                    function matchText(t) {
                        const low = t.toLowerCase();
                        for (const tr of triggers) {
                            if (low.includes(tr.toLowerCase())) return true;
                        }
                        return false;
                    }

                    function scanRoot(root, inResult) {
                        if (!root) return null;
                        const nodes = root.querySelectorAll('*');
                        for (const el of nodes) {
                            const tag = (el.tagName || '').toLowerCase();
                            if (tag === 'textarea' || tag === 'input' || tag === 'script' || tag === 'style' || tag === 'template') continue;
                            const t = (el.textContent || '').trim();
                            if (!t || t.length > 1000) continue;
                            if (!matchText(t)) continue;
                            if (!isTrulyVisible(el)) continue;
                            return { text: t.slice(0, 300), in_result: inResult };
                        }
                        return null;
                    }

                    function findResultRoot() {
                        const kws = ['Результат генерации', 'Результат', 'Result'];
                        let best = null;
                        let bestArea = 0;
                        for (const el of document.querySelectorAll('section, div, article, main')) {
                            const t = (el.textContent || '').trim();
                            if (!t || t.length > 1200) continue;
                            for (const kw of kws) {
                                if (!t.includes(kw)) continue;
                                const r = el.getBoundingClientRect();
                                const area = r.width * r.height;
                                if (area > bestArea && r.width >= 120 && r.height >= 60) {
                                    best = el;
                                    bestArea = area;
                                }
                            }
                        }
                        return best;
                    }

                    const resultRoot = findResultRoot();
                    if (resultRoot) {
                        const inPanel = scanRoot(resultRoot, true);
                        if (inPanel) return inPanel;
                    }
                    return scanRoot(document.body, false);
                }""",
                {"moderation": mod_js, "generation": gen_js},
            )
            if isinstance(raw, dict) and raw.get("text"):
                text = str(raw["text"]).strip()
                if text:
                    return {
                        "text": text,
                        "in_result": bool(raw.get("in_result")),
                    }
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _outsee_failure_text(self, page: Page) -> str | None:
        """Текст видимой плашки ошибки (любой kind) или None."""
        hit = await self._detect_outsee_failure(page)
        if hit:
            return str(hit["text"])
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
        gen_id: str | None = None,
        model_slug: str | None = None,
        resolution: str | None = None,
        relax: bool = False,
        prompt_id_prefix: str | None = None,
        project_id: int | None = None,
    ) -> GenerationResult:
        import uuid as _uuid

        from app.services.step_cancel import abort_if_cancelled

        abort_if_cancelled(project_id)
        gen_id = gen_id or _uuid.uuid4().hex
        if prompt_id_prefix:
            from app.generation_options import prepend_gen_id

            prompt = prepend_gen_id(prompt, prompt_id_prefix)
            logger.info(
                "outsee.generate_video: prompt_id_prefix={}", prompt_id_prefix
            )
        page_url = _video_page_url(model_slug)
        logger.info(
            "outsee.generate_video: gen_id={} url={}", gen_id[:8], page_url
        )
        page = await self.session.open_page(page_url, reuse=True)
        from app.services.step_cancel import register_active_page, unregister_active_page

        if project_id is not None:
            register_active_page(project_id, page)
        try:
            return await self._generate_video_on_page(
                page,
                prompt=prompt,
                out_path=out_path,
                start_frame=start_frame,
                aspect_ratio=aspect_ratio,
                timeout=timeout,
                gen_id=gen_id,
                model_slug=model_slug,
                resolution=resolution,
                relax=relax,
                prompt_id_prefix=prompt_id_prefix,
                project_id=project_id,
                page_url=page_url,
            )
        finally:
            if project_id is not None:
                unregister_active_page(project_id)

    async def _generate_video_on_page(
        self,
        page: Any,
        *,
        prompt: str,
        out_path: Path,
        start_frame: Path | None,
        aspect_ratio: str,
        timeout: float,
        gen_id: str,
        model_slug: str | None,
        resolution: str | None,
        relax: bool,
        prompt_id_prefix: str | None,
        project_id: int | None,
        page_url: str,
    ) -> GenerationResult:
        """Тело generate_video — зеркало _generate_image_on_page."""
        import time as _time

        from app.services.step_cancel import (
            StepCancelledError,
            abort_if_cancelled,
            await_with_cancel,
            sleep_cancellable,
        )

        abort_if_cancelled(project_id)
        dumps: list[Path] = []
        page_base = page_url.split("?", 1)[0]
        cur_base = (page.url or "").split("?", 1)[0]
        if cur_base != page_base:
            try:
                await await_with_cancel(
                    page.goto(page_url, wait_until="domcontentloaded"), project_id
                )
            except StepCancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "outsee.generate_video: page.goto({}) упал: {} — продолжаю",
                    page_url,
                    e,
                )
        else:
            logger.info(
                "outsee.generate_video: та же вкладка outsee video — без reload"
            )
        await await_with_cancel(page.wait_for_load_state("domcontentloaded"), project_id)
        try:
            await await_with_cancel(
                page.wait_for_load_state("networkidle", timeout=15_000), project_id
            )
        except StepCancelledError:
            raise
        except Exception:
            pass
        abort_if_cancelled(project_id)
        logger.info("outsee.generate_video: страница готова")

        baseline_video_urls = {
            _strip_url_query(u)
            for u in await self._all_video_urls_on_page(page)
            if u and _video_url_looks_like_result(u)
        }
        logger.info(
            "outsee.generate_video: baseline video_urls={}",
            len(baseline_video_urls),
        )

        click_ts = _time.monotonic()
        net_events: list[tuple[float, str]] = []

        def _on_response(resp: Any) -> None:
            try:
                if not _is_candidate_video_response(resp):
                    return
                net_events.append((_time.monotonic() - click_ts, resp.url))
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_response)

        try:
            pre_rejected_text: str | None = None

            input_sel = await _first_visible(
                page, PROMPT_INPUT_SELECTORS, timeout_ms=60_000, project_id=project_id
            )
            if not input_sel:
                h, p = await _dump_page(page, "video_prompt_input_notfound")
                for x in (h, p):
                    if x:
                        dumps.append(x)
                raise OutseeImageError(
                    "outsee video: не найден ввод промта",
                    context={"gen_id": gen_id},
                    dumps=dumps,
                )
            prompt_loc = page.locator(input_sel).first
            try:
                await await_with_cancel(
                    prompt_loc.scroll_into_view_if_needed(timeout=5_000),
                    project_id,
                )
            except Exception:  # noqa: BLE001
                pass

            # --- Порядок veo: кадр → настройки (physical) → Relax → промт → Generate
            if start_frame is not None:
                if not start_frame.exists():
                    raise OutseeImageError(
                        f"outsee video: start_frame не найден: {start_frame}",
                        context={"gen_id": gen_id},
                        dumps=dumps,
                    )
                attached = await self._attach_ref_image_robust(
                    page,
                    start_frame,
                    where="generate_video[start_frame]",
                    project_id=project_id,
                    prefer_first=True,
                )
                if not attached:
                    h, p = await _dump_page(page, "video_start_frame_notfound")
                    for x in (h, p):
                        if x:
                            dumps.append(x)
                    raise OutseeImageError(
                        "outsee video: не удалось загрузить стартовый кадр",
                        context={"gen_id": gen_id},
                        dumps=dumps,
                    )
                await sleep_cancellable(1.0, project_id)
                logger.info("outsee.generate_video: стартовый кадр загружен")

            if aspect_ratio:
                await _select_aspect_ratio(
                    page,
                    aspect_ratio,
                    where="generate_video",
                    dumps=dumps,
                    project_id=project_id,
                )
                asp_sel = await _first_visible(
                    page,
                    _aspect_selectors(aspect_ratio),
                    timeout_ms=2_000,
                    project_id=project_id,
                )
                if asp_sel:
                    try:
                        await _physical_mouse_click(
                            page,
                            page.locator(asp_sel).first,
                            project_id=project_id,
                            label=f"aspect {aspect_ratio}",
                        )
                    except Exception:  # noqa: BLE001
                        pass

            if resolution:
                res_sel = await _first_visible(
                    page,
                    _resolution_selectors(resolution),
                    timeout_ms=5_000,
                    project_id=project_id,
                )
                if res_sel:
                    try:
                        await _physical_mouse_click(
                            page,
                            page.locator(res_sel).first,
                            project_id=project_id,
                            label=f"resolution {resolution}",
                        )
                        logger.info(
                            "outsee.generate_video: {} physical ({})",
                            resolution,
                            res_sel,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "outsee.generate_video: resolution {}: {}",
                            resolution,
                            e,
                        )

            await self._ensure_relax_for_video(
                page,
                want_on=relax,
                where="generate_video",
                project_id=project_id,
                dumps=dumps,
            )
            abort_if_cancelled(project_id)

            await await_with_cancel(prompt_loc.click(), project_id)
            await await_with_cancel(prompt_loc.fill(prompt), project_id)
            try:
                await await_with_cancel(prompt_loc.press("Tab"), project_id)
            except Exception:  # noqa: BLE001
                pass
            await _verify_composer_prompt_filled(
                page,
                input_sel,
                expected_prompt=prompt,
                prompt_id_prefix=prompt_id_prefix,
                where="generate_video",
            )
            actual_len = len(await _read_composer_prompt_value(page, input_sel))
            logger.info(
                "outsee.generate_video: промт в поле ввода (отправлено {} симв, "
                "в textarea {} симв)",
                len(prompt),
                actual_len,
            )
            abort_if_cancelled(project_id)

            gen_sel = await _first_visible(
                page,
                GENERATE_BUTTON_SELECTORS,
                timeout_ms=15_000,
                project_id=project_id,
            )
            if not gen_sel:
                h, p = await _dump_page(page, "video_generate_notfound_pre")
                for x in (h, p):
                    if x:
                        dumps.append(x)
                raise OutseeImageError(
                    "outsee video: кнопка Generate не найдена до клика",
                    context={"gen_id": gen_id},
                    dumps=dumps,
                )
            logger.info(
                "outsee.generate_video: Generate на экране ({})", gen_sel
            )
            try:
                await self._wait_button_enabled(
                    page, gen_sel, timeout_s=180, project_id=project_id
                )
            except Exception as e:  # noqa: BLE001
                raise OutseeImageError(
                    "outsee video: Generate не стала активной (промт/кадр/Relax?)",
                    context={
                        "gen_id": gen_id,
                        "gen_sel": gen_sel,
                        "relax": relax,
                        "err": str(e),
                    },
                    dumps=dumps,
                ) from e

            baseline_video_urls = {
                _strip_url_query(u)
                for u in await self._all_video_urls_on_page(page)
                if u and _video_url_looks_like_result(u)
            }
            logger.info(
                "outsee.generate_video: re-baseline перед Generate video_urls={}",
                len(baseline_video_urls),
            )

            pre_rejected_text = await self._outsee_failure_text(page)
            if pre_rejected_text:
                logger.info(
                    "outsee.generate_video: pre-click failure_text ({} симв, "
                    "kind={})",
                    len(pre_rejected_text),
                    _outsee_failure_kind(pre_rejected_text),
                )

            click_ts = _time.monotonic()
            net_events.clear()
            await self._trigger_generate_video(
                page,
                input_sel=input_sel,
                project_id=project_id,
                dumps=dumps,
                context={"gen_id": gen_id, "prompt_id": prompt_id_prefix},
            )
            logger.info(
                "outsee.generate_video: Generate запущен, жду ролик "
                "(gen_id={})",
                gen_id[:8],
            )

            try:
                video_url = await self._wait_video_url_strict(
                    page,
                    timeout=timeout,
                    baseline_video_urls=baseline_video_urls,
                    net_events=net_events,
                    gen_id=gen_id,
                    pre_rejected_text=pre_rejected_text,
                    prompt_id_prefix=prompt_id_prefix,
                    project_id=project_id,
                )
            except OutseeContentRejectedError as e:
                e.dumps = list(dumps)
                raise
            except OutseeImageError as e:
                h, p = await _dump_page(page, "video_timeout")
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

        failure_after = await self._detect_outsee_failure(page)
        if failure_after:
            ftext = failure_after["text"]
            in_result = bool(failure_after.get("in_result"))
            is_new = (
                in_result
                or not pre_rejected_text
                or ftext != pre_rejected_text
            )
            if is_new:
                _raise_outsee_failure(
                    text=ftext,
                    gen_id=gen_id,
                    elapsed=0.0,
                    in_result=in_result,
                )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if prompt_id_prefix:
                await _download_via_video_card_click(
                    page,
                    prompt_id_prefix=prompt_id_prefix,
                    out_path=out_path,
                    video_url=video_url,
                    project_id=project_id,
                )
            else:
                await _download_via_context(
                    page, video_url, out_path, project_id=project_id
                )
        except OutseeImageError as e:
            e.context.setdefault("gen_id", gen_id)
            e.context.setdefault("video_url", video_url)
            e.dumps = list(dumps)
            raise
        except Exception as e:  # noqa: BLE001
            raise OutseeImageError(
                "outsee video: скачивание результата упало",
                context={
                    "gen_id": gen_id,
                    "video_url": video_url,
                    "err": f"{type(e).__name__}: {e}",
                },
                dumps=dumps,
            ) from e

        logger.info("outsee video saved → {} (gen_id={})", out_path, gen_id[:8])
        gen_sel_done = await _first_visible(
            page, GENERATE_BUTTON_SELECTORS, timeout_ms=5_000, project_id=project_id
        )
        if gen_sel_done:
            try:
                await self._wait_button_enabled(
                    page, gen_sel_done, timeout_s=120, project_id=project_id
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "outsee.generate_video: Generate не стал активным после "
                    "скачивания — продолжаю"
                )
        return GenerationResult(
            file_path=out_path,
            raw_url=video_url,
            gen_id=gen_id,
            dumps=dumps or None,
        )

    async def _find_video_by_prompt_id(
        self,
        page: Page,
        id_token: str,
        *,
        max_levels: int = 12,
    ) -> str | None:
        """Зеркало `_find_img_by_prompt_id`, но для `<video>` / `<source>` /
        `a[download]`. Ищет в DOM карточку с нашим `[ID: ...]` и возвращает
        URL ближайшего видео-элемента.

        Порядок матчинга такой же:
          1) полный `[ID: P*-F*-xxxxxxxx]`,
          2) `P*-F*-xxxxxxxx` (без скобок и `ID:`),
          3) 8-hex-tail (`xxxxxxxx`).
        """
        tokens: list[str] = [id_token]
        m = re.search(r"\[ID:\s*([A-Za-z0-9_-]+)\s*\]", id_token)
        if m:
            inner = m.group(1)
            if inner not in tokens:
                tokens.append(inner)
        m2 = re.search(r"-([0-9a-fA-F]{8})\]?$", id_token)
        if m2:
            tail = m2.group(1)
            if tail and tail not in tokens:
                tokens.append(tail)

        js = """
        ([tokens, maxLevels]) => {
            const hasToken = (el, idToken) => {
                if (!el) return false;
                const t = (el.innerText || el.textContent || '');
                if (t.includes(idToken)) return true;
                const tag = el.tagName && el.tagName.toLowerCase();
                if (tag === 'textarea' || tag === 'input') {
                    const v = el.value || '';
                    if (v.includes(idToken)) return true;
                }
                return false;
            };
            const pickVideoSrc = (root) => {
                // Сначала <video src>, потом <source src>, потом
                // a[download] с .mp4 — последний наименее надёжен,
                // зато подхватывает уже-готовый скачиваемый ролик.
                const videos = root.querySelectorAll('video');
                for (const v of videos) {
                    if (v.src && !v.src.startsWith('data:')) return v.src;
                    const sources = v.querySelectorAll('source');
                    for (const s of sources) {
                        if (s.src && !s.src.startsWith('data:')) return s.src;
                    }
                }
                const links = root.querySelectorAll('a[download], a[href*=".mp4"]');
                for (const a of links) {
                    if (a.href && !a.href.startsWith('data:')) return a.href;
                }
                return null;
            };
            for (const idToken of tokens) {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (!el || !el.children) continue;
                    if (el === document.body || el === document.documentElement) continue;
                    const tag0 = el.tagName && el.tagName.toLowerCase();
                    if (tag0 === 'textarea' || tag0 === 'input') continue;
                    if (!hasToken(el, idToken)) continue;
                    let smallest = el;
                    for (const child of el.children) {
                        if (hasToken(child, idToken)) { smallest = null; break; }
                    }
                    if (smallest) {
                        const deepInputs = el.querySelectorAll('textarea, input');
                        for (const di of deepInputs) {
                            if (di === el) continue;
                            const v = di.value || '';
                            if (v.includes(idToken)) { smallest = null; break; }
                        }
                    }
                    if (!smallest) continue;
                    let cur = smallest;
                    for (let i = 0; i < maxLevels && cur; i++) {
                        const src = pickVideoSrc(cur);
                        if (src) return src;
                        cur = cur.parentElement;
                    }
                }
            }
            return null;
        }
        """
        try:
            res = await page.evaluate(js, [tokens, max_levels])
            if isinstance(res, str) and res:
                return res
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_find_video_by_prompt_id: ошибка JS-поиска: {}", e
            )
            return None

    async def _all_video_urls_on_page(self, page: Page) -> list[str]:
        try:
            urls = await page.evaluate(
                """() => {
                    const list = [];
                    document.querySelectorAll('video').forEach(v => {
                        if (v.src) list.push(v.src);
                        v.querySelectorAll('source').forEach(s => {
                            if (s.src) list.push(s.src);
                        });
                    });
                    document.querySelectorAll("a[download], a[href*='.mp4']").forEach(a => {
                        if (a.href) list.push(a.href);
                    });
                    return list;
                }"""
            )
            return [u for u in (urls or []) if isinstance(u, str) and u]
        except Exception:  # noqa: BLE001
            return []

    async def _wait_video_url_strict(
        self,
        page: Page,
        *,
        timeout: float,
        baseline_video_urls: set[str],
        net_events: list[tuple[float, str]] | None = None,
        gen_id: str,
        pre_rejected_text: str | None = None,
        prompt_id_prefix: str | None = None,
        project_id: int | None = None,
    ) -> str:
        """Жёсткое ожидание свежего ролика — зеркало _wait_image_url_strict."""
        start = asyncio.get_event_loop().time()
        deadline = start + timeout
        last_log = 0.0
        fallback_candidate: str | None = None
        fallback_source: str | None = None
        rejected_candidates: set[str] = set()
        _MIN_SEC_BEFORE_HANDOFF = 6.0

        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
            now = asyncio.get_event_loop().time()
            elapsed = now - start

            if elapsed >= 1.5:
                failure = await self._detect_outsee_failure(page)
                if failure:
                    ftext = failure["text"]
                    in_result = bool(failure.get("in_result"))
                    is_new = (
                        in_result
                        or not pre_rejected_text
                        or ftext != pre_rejected_text
                    )
                    if is_new:
                        logger.info(
                            "_wait_video_url_strict: ошибка outsee за {:.0f} сек "
                            "(in_result={}, kind={}): {}",
                            elapsed,
                            in_result,
                            _outsee_failure_kind(ftext),
                            ftext[:120],
                        )
                        _raise_outsee_failure(
                            text=ftext,
                            gen_id=gen_id,
                            elapsed=elapsed,
                            in_result=in_result,
                        )

            if prompt_id_prefix:
                by_id = await self._find_video_by_prompt_id(
                    page, prompt_id_prefix
                )
                if by_id and _video_url_looks_like_result(by_id):
                    by_id_norm = _strip_url_query(by_id)
                    fresh_ok = by_id_norm not in baseline_video_urls
                    if fresh_ok:
                        if (not net_events) or _url_is_fresh(by_id, net_events):
                            logger.info(
                                "_wait_video_url_strict: matched by prompt_id "
                                "{} за {:.0f} сек: {}",
                                prompt_id_prefix,
                                elapsed,
                                by_id[:140],
                            )
                            return by_id

            new_videos = await self._completed_new_videos(
                page, baseline_video_urls
            )
            if new_videos:
                clean = list(new_videos)
                if net_events:
                    clean = [u for u in clean if _url_is_fresh(u, net_events)]
                if prompt_id_prefix:
                    clean = [
                        u
                        for u in clean
                        if _strip_url_query(u) not in rejected_candidates
                    ]
                if clean:
                    chosen = clean[0]
                    if not prompt_id_prefix:
                        logger.info(
                            "_wait_video_url_strict: новый ролик в DOM за "
                            "{:.0f} сек: {} (всего новых: {})",
                            elapsed,
                            chosen[:140],
                            len(clean),
                        )
                        return chosen
                    if _strip_url_query(chosen) not in rejected_candidates:
                        fallback_candidate = chosen
                        fallback_source = "new_dom"
                        if len(clean) > 1:
                            logger.info(
                                "_wait_video_url_strict: new_videos={} (>1) — "
                                "беру первый: {}",
                                len(clean),
                                chosen[:120],
                            )

            if (
                prompt_id_prefix
                and fallback_candidate is not None
                and _strip_url_query(fallback_candidate)
                not in rejected_candidates
            ):
                gen_idle = await self._generate_button_enabled(page)
                if gen_idle and elapsed >= _MIN_SEC_BEFORE_HANDOFF:
                    if (not net_events) or _url_is_fresh(
                        fallback_candidate, net_events
                    ):
                        logger.info(
                            "_wait_video_url_strict: gen завершена, handoff "
                            "download-v10video (source={}, {:.0f} сек)",
                            fallback_source,
                            elapsed,
                        )
                        return fallback_candidate

            if (
                prompt_id_prefix
                and elapsed >= _MIN_SEC_BEFORE_HANDOFF
                and await self._generate_button_enabled(page)
            ):
                idle_srcs = await self._completed_new_videos(
                    page, baseline_video_urls
                )
                if idle_srcs:
                    idle_clean = list(idle_srcs)
                    if net_events:
                        idle_clean = [
                            u for u in idle_clean if _url_is_fresh(u, net_events)
                        ]
                    if idle_clean:
                        logger.info(
                            "_wait_video_url_strict: gen_idle, handoff "
                            "download-v10video за {:.0f} сек",
                            elapsed,
                        )
                        return idle_clean[0]

            if elapsed - last_log > 15:
                last_log = elapsed
                urls = await self._all_video_urls_on_page(page)
                logger.info(
                    "_wait_video_url_strict: ждём... {:.0f} сек, "
                    "videos_in_dom={}, fallback={}",
                    elapsed,
                    len(urls),
                    (
                        fallback_candidate[:80]
                        if fallback_candidate
                        else None
                    ),
                )

            await sleep_cancellable(1.0, project_id)

        ctx: dict[str, Any] = {
            "gen_id": gen_id,
            "baseline_video_urls": len(baseline_video_urls),
            "rejected_count": len(rejected_candidates),
        }
        if prompt_id_prefix:
            ctx["prompt_id_prefix"] = prompt_id_prefix
            ctx["id_diag"] = await self._diag_id_in_page(page, prompt_id_prefix)
            if await self._generate_button_enabled(page):
                handoff_srcs = await self._completed_new_videos(
                    page, baseline_video_urls
                )
                if handoff_srcs:
                    chosen = handoff_srcs[0]
                    logger.warning(
                        "_wait_video_url_strict: timeout {:.0f}с, но gen_idle "
                        "и есть новые ролики — handoff: {}",
                        timeout,
                        chosen[:120],
                    )
                    return chosen
        raise OutseeImageError(
            f"outsee video: результат не появился за {int(timeout)} сек",
            context=ctx,
        )

    async def _wait_video_url(
        self,
        page: Page,
        *,
        timeout: float,
        prompt_id_prefix: str | None = None,
        project_id: int | None = None,
    ) -> str:
        """Legacy: recon / без prompt_id — любой mp4 в DOM."""
        baseline = {
            _strip_url_query(u) for u in await self._all_video_urls_on_page(page) if u
        }
        if prompt_id_prefix:
            return await self._wait_video_url_strict(
                page,
                timeout=timeout,
                baseline_video_urls=baseline,
                prompt_id_prefix=prompt_id_prefix,
                gen_id="legacy",
                project_id=project_id,
            )
        deadline = asyncio.get_event_loop().time() + timeout
        from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

        while asyncio.get_event_loop().time() < deadline:
            abort_if_cancelled(project_id)
            for u in await self._all_video_urls_on_page(page):
                if any(tok in u for tok in (".mp4", "blob:", "video", "cdn", "storage")):
                    return u
            await sleep_cancellable(1.5, project_id)
        raise PWTimeoutError("outsee video: результат не появился за отведённое время")


def _prompt_id_search_tokens(prompt_id_prefix: str) -> list[str]:
    """Токены для поиска карточки по `[ID: …]` (включая retry `r2a1`)."""
    tokens: list[str] = [prompt_id_prefix]
    m = re.search(
        r"\[ID:\s*([A-Za-z0-9_-]+)(?:\s+r\d+a\d+)?\s*\]",
        prompt_id_prefix,
    )
    if m:
        inner = m.group(1)
        if inner not in tokens:
            tokens.append(inner)
    m2 = re.search(
        r"-([0-9a-fA-F]{8})(?:\s+r\d+a\d+)?\]?$",
        prompt_id_prefix,
    )
    if m2:
        tail = m2.group(1)
        if tail and tail not in tokens:
            tokens.append(tail)
    return tokens


def _count_tokens_in_text(text: str, tokens: list[str]) -> int:
    return sum(text.count(tok) for tok in tokens if tok)


def _verify_prompt_length_before_send(full_prompt: str, *, where: str) -> None:
    """Outsee отклоняет или молча обрезает промты длиннее лимита."""
    from app.generation_options import OUTSEE_PROMPT_MAX_CHARS

    n = len(full_prompt)
    if n > OUTSEE_PROMPT_MAX_CHARS:
        raise OutseeImageError(
            f"outsee: промт {n} симв — лимит outsee {OUTSEE_PROMPT_MAX_CHARS}. "
            "Сожмите image_prompt (шаг «Промты картинок») или дождитесь "
            "GPT-сжатия в retry.",
            context={
                "where": where,
                "prompt_len": n,
                "limit": OUTSEE_PROMPT_MAX_CHARS,
            },
        )


async def _read_composer_prompt_value(page: Page, input_sel: str) -> str:
    loc = page.locator(input_sel).first
    try:
        val = await loc.input_value()
        if val and val.strip():
            return val.strip()
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = await loc.evaluate(
            """el => {
                const t = (el.tagName || '').toLowerCase();
                if (t === 'textarea' || t === 'input') return el.value || '';
                return el.innerText || el.textContent || '';
            }"""
        )
        return raw.strip() if isinstance(raw, str) else ""
    except Exception:  # noqa: BLE001
        return ""


async def _verify_composer_prompt_filled(
    page: Page,
    input_sel: str,
    *,
    expected_prompt: str,
    prompt_id_prefix: str | None,
    where: str,
) -> None:
    """После fill(): outsee иногда не принимает длинный промт — не ждём таймаут."""
    from app.generation_options import OUTSEE_PROMPT_MAX_CHARS

    actual = await _read_composer_prompt_value(page, input_sel)
    exp_len = len(expected_prompt)
    if len(actual) < 20:
        raise OutseeImageError(
            "outsee: промт не попал в поле ввода (пусто или слишком коротко)",
            context={
                "where": where,
                "actual_len": len(actual),
                "expected_len": exp_len,
            },
        )
    if prompt_id_prefix:
        tokens = _prompt_id_search_tokens(prompt_id_prefix)
        if not any(tok in actual for tok in tokens if len(tok) >= 6):
            raise OutseeImageError(
                "outsee: ID промта не найден в поле после вставки",
                context={
                    "where": where,
                    "prompt_id_prefix": prompt_id_prefix,
                    "actual_len": len(actual),
                },
            )
    ref_len = min(exp_len, OUTSEE_PROMPT_MAX_CHARS)
    if ref_len >= 200 and len(actual) < int(ref_len * 0.85):
        raise OutseeImageError(
            f"outsee: промт обрезан outsee ({len(actual)} из {exp_len} симв)",
            context={
                "where": where,
                "actual_len": len(actual),
                "expected_len": exp_len,
            },
        )


async def _viewport_mouse_click(
    page: Page,
    x: float,
    y: float,
    *,
    project_id: int | None = None,
    label: str = "",
) -> None:
    from app.services.step_cancel import await_with_cancel

    await await_with_cancel(page.mouse.move(x, y), project_id)
    await asyncio.sleep(0.05)
    await await_with_cancel(
        page.mouse.click(x, y, delay=80), project_id
    )
    logger.info(
        "outsee viewport-click: ({:.0f},{:.0f}){}",
        x,
        y,
        f" — {label}" if label else "",
    )


async def _cdp_dispatch_click(
    page: Page,
    x: float,
    y: float,
    *,
    project_id: int | None = None,
) -> None:
    """Клик через Chrome DevTools Protocol (обходит часть overlay/React)."""
    from app.services.step_cancel import await_with_cancel

    try:
        session = await page.context.new_cdp_session(page)
        for ev_type, button_state in (
            ("mouseMoved", 0),
            ("mousePressed", 1),
            ("mouseReleased", 1),
        ):
            await await_with_cancel(
                session.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": ev_type,
                        "x": x,
                        "y": y,
                        "button": "left",
                        "buttons": button_state,
                        "clickCount": 1,
                    },
                ),
                project_id,
            )
            await asyncio.sleep(0.04)
        logger.info("outsee cdp-click: ({:.0f},{:.0f})", x, y)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "outsee cdp-click failed ({}), fallback mouse", type(e).__name__
        )
        await _viewport_mouse_click(
            page, x, y, project_id=project_id, label="cdp-fallback"
        )


async def _physical_mouse_click(
    page: Page,
    locator: Any,
    *,
    project_id: int | None = None,
    label: str = "",
) -> None:
    """Реальный клик мышью по центру элемента (CDP → Chrome).

    Outsee открывает панель «Промпт» и кнопку Download только на pointer-
    событиях; `element.click()` в JS или «сухой» locator иногда не срабатывает.
    """
    from app.services.step_cancel import await_with_cancel

    with contextlib.suppress(Exception):
        await await_with_cancel(
            locator.scroll_into_view_if_needed(timeout=2_000),
            project_id,
        )
    box = await locator.bounding_box()
    if not box or box.get("width", 0) < 2 or box.get("height", 0) < 2:
        await await_with_cancel(locator.click(timeout=3_000), project_id)
        logger.info(
            "outsee physical-click: fallback locator.click{}",
            f" ({label})" if label else "",
        )
        return
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await asyncio.sleep(0.05)
    await page.mouse.click(x, y)
    logger.info(
        "outsee physical-click: mouse ({:.0f},{:.0f}){}",
        x,
        y,
        f" — {label}" if label else "",
    )


async def _gallery_detail_panel_has_id(
    page: Page,
    prompt_id_prefix: str,
) -> bool:
    """После клика по thumb: наш ID в правой панели (НЕ в composer).

    Нельзя использовать `post_count > pre_count` на всей странице — ID уже
    в composer, счётчик не растёт. Нельзя `evaluate(el.click())` — не мышь.
    """
    tokens = _prompt_id_search_tokens(prompt_id_prefix)
    try:
        matched = await page.evaluate(
            """([tokens, composerSels]) => {
                const composer = new Set();
                for (const sel of composerSels) {
                    try {
                        for (const el of document.querySelectorAll(sel))
                            composer.add(el);
                    } catch (e) {}
                }
                function visible(el) {
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 8 && r.height > 8;
                }
                // Phase 1: non-composer textarea/input (strictest — the
                // detail panel usually renders prompt in a readonly textarea)
                for (const el of document.querySelectorAll('textarea, input')) {
                    if (composer.has(el) || !visible(el)) continue;
                    const v = (el.value || el.innerText || '').trim();
                    if (!v) continue;
                    for (const tok of tokens) {
                        if (tok && v.includes(tok)) return true;
                    }
                }
                // Phase 2: panel-like containers — widened selector set
                // and lowered midX threshold (outsee may render the panel
                // closer to center on narrow viewports)
                const midX = window.innerWidth * 0.25;
                const panelSels = [
                    'section', 'aside',
                    'div[role="dialog"]', 'div[role="complementary"]',
                    '[class*="detail"]', '[class*="Detail"]',
                    '[class*="panel"]', '[class*="Panel"]',
                    '[class*="prompt"]', '[class*="Prompt"]',
                    '[class*="sidebar"]', '[class*="Sidebar"]',
                    '[data-panel]', '[data-testid*="detail"]',
                    'details',
                ].join(', ');
                for (const el of document.querySelectorAll(panelSels)) {
                    if (!visible(el)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.left < midX || r.width < 60) continue;
                    const t = (el.innerText || '').trim();
                    if (t.length < 20 || t.length > 15000) continue;
                    for (const tok of tokens) {
                        if (tok && t.includes(tok)) return true;
                    }
                }
                // Phase 3: any visible <pre>, <code>, <p> or <span> that
                // appeared in the right half of the viewport (outsee may
                // render the prompt in a non-standard container)
                for (const el of document.querySelectorAll('pre, code, p, span')) {
                    if (!visible(el)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.left < midX || r.width < 40) continue;
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t.length < 10 || t.length > 15000) continue;
                    for (const tok of tokens) {
                        if (tok && t.includes(tok)) return true;
                    }
                }
                return false;
            }""",
            [tokens, PROMPT_INPUT_SELECTORS],
        )
        return bool(matched)
    except Exception:  # noqa: BLE001
        return False


async def _count_gallery_id_matches(
    page: Page,
    prompt_id_prefix: str,
) -> int:
    """Count how many gallery cards contain our prompt ID.

    Returns the number of *distinct* gallery cards (big images ≥180×180)
    whose associated text (visible text + textarea/input values within
    the card's ancestor tree) contains at least one of the ID tokens.

    Used for post-download verification: exactly 1 match means the
    download was unambiguous; 0 means outsee scrolled the card away;
    >1 means there's an ID collision / ambiguity.
    """
    tokens = _prompt_id_search_tokens(prompt_id_prefix)
    try:
        count = await page.evaluate(
            """([tokens]) => {
                const hasToken = (text) => {
                    for (const tok of tokens) {
                        if (tok && text.includes(tok)) return true;
                    }
                    return false;
                };
                let matches = 0;
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const r = img.getBoundingClientRect();
                    if (r.width < 180 || r.height < 180 || !img.src) continue;
                    // Walk up to find a card-like ancestor and check its text
                    let cur = img;
                    let found = false;
                    for (let i = 0; i < 12 && cur && !found; i++) {
                        const t = (cur.innerText || cur.textContent || '');
                        if (hasToken(t)) { found = true; break; }
                        // Also check textarea/input values inside
                        for (const el of cur.querySelectorAll('textarea, input')) {
                            const v = el.value || '';
                            if (hasToken(v)) { found = true; break; }
                        }
                        cur = cur.parentElement;
                    }
                    if (found) matches++;
                }
                return matches;
            }""",
            [tokens],
        )
        return int(count) if isinstance(count, int) else 0
    except Exception:  # noqa: BLE001
        return -1


async def _page_text_excluding_composer(
    page: Page,
    composer_selectors: list[str] | None = None,
) -> str:
    selectors = composer_selectors or PROMPT_INPUT_SELECTORS
    try:
        res = await page.evaluate(
            """(selectors) => {
                const composer = new Set();
                for (const sel of selectors) {
                    try {
                        for (const el of document.querySelectorAll(sel)) {
                            composer.add(el);
                        }
                    } catch (e) {}
                }
                let text = (document.body && (
                    document.body.innerText || document.body.textContent
                )) || '';
                for (const el of document.querySelectorAll(
                    'textarea, input[type=text], input:not([type])'
                )) {
                    if (composer.has(el)) continue;
                    const v = el && el.value;
                    if (v) text += '\\n' + v;
                }
                return text;
            }""",
            selectors,
        )
        return res if isinstance(res, str) else ""
    except Exception:  # noqa: BLE001
        return ""


async def _count_gallery_video_thumbs(page: Page) -> int:
    """Большие `<video>` в галерее (превью роликов)."""
    try:
        n = await page.evaluate(
            """() => {
                let c = 0;
                for (const v of document.querySelectorAll('video')) {
                    const r = v.getBoundingClientRect();
                    if (r.width >= 120 && r.height >= 120) c++;
                }
                return c;
            }"""
        )
        return int(n) if isinstance(n, int) else 0
    except Exception:  # noqa: BLE001
        return 0


async def _wait_gallery_video_thumbs(
    page: Page,
    *,
    min_count: int = 1,
    timeout_s: float = 45.0,
    project_id: int | None = None,
) -> int:
    from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout_s:
        abort_if_cancelled(project_id)
        n = await _count_gallery_video_thumbs(page)
        if n >= min_count:
            return n
        await sleep_cancellable(1.0, project_id)
    return await _count_gallery_video_thumbs(page)


async def _find_card_by_clicking_videos(
    page: Page,
    *,
    prompt_id_prefix: str,
    limit: int = 10,
    project_id: int | None = None,
) -> Any | None:
    """Перебор первых N роликов в галерее: physical click → ID в панели."""
    from app.services.step_cancel import abort_if_cancelled

    abort_if_cancelled(project_id)
    try:
        srcs: list[str] = await page.evaluate(
            """() => {
                const out = [];
                for (const v of document.querySelectorAll('video')) {
                    const r = v.getBoundingClientRect();
                    if (r.width < 120 || r.height < 120) continue;
                    const src = v.currentSrc || v.src || '';
                    if (src && !src.startsWith('data:')) out.push(src);
                }
                return out;
            }"""
        )
    except Exception:  # noqa: BLE001
        srcs = []

    if not srcs:
        logger.info(
            "_find_card_by_clicking_videos: нет video thumb — fallback на img"
        )
        return await _find_card_by_clicking_images(
            page,
            prompt_id_prefix=prompt_id_prefix,
            limit=limit,
            project_id=project_id,
        )

    logger.info(
        "_find_card_by_clicking_videos: перебор {} роликов (max {})",
        len(srcs),
        limit,
    )

    for idx, src in enumerate(srcs[:limit]):
        abort_if_cancelled(project_id)
        stripped = _strip_url_query(src)
        path_only = re.sub(r"^https?://[^/]+", "", stripped)
        if not path_only:
            continue
        basename = Path(path_only).name
        vid_loc = None
        for fragment in (basename, path_only):
            if not fragment:
                continue
            loc = page.locator(f'video[src*="{fragment}"]').first
            if await loc.count() > 0:
                vid_loc = loc
                break
        if vid_loc is None:
            loc = page.locator("video").nth(idx)
            if await loc.count() == 0:
                continue
            vid_loc = loc

        try:
            await _physical_mouse_click(
                page,
                vid_loc,
                project_id=project_id,
                label=f"gallery video #{idx}",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_find_card_by_clicking_videos: click video #{} ({})",
                idx,
                type(e).__name__,
            )
            continue

        await asyncio.sleep(0.65)
        if not await _gallery_detail_panel_has_id(page, prompt_id_prefix):
            with contextlib.suppress(Exception):
                await page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
            continue

        candidate = vid_loc.locator(
            "xpath=ancestor::*[descendant::button"
            "[descendant::svg[contains(@class,'lucide-download')]]][1]"
        )
        if await candidate.count() > 0:
            logger.info(
                "_find_card_by_clicking_videos: НАШЛИ ролик #{} по ID в панели",
                idx,
            )
            return candidate
        with contextlib.suppress(Exception):
            await page.keyboard.press("Escape")

    logger.warning(
        "_find_card_by_clicking_videos: {} роликов без нашего ID — пробую img",
        min(len(srcs), limit),
    )
    return await _find_card_by_clicking_images(
        page,
        prompt_id_prefix=prompt_id_prefix,
        limit=limit,
        project_id=project_id,
    )


async def _download_via_video_card_click(
    page: Page,
    *,
    prompt_id_prefix: str,
    out_path: Path,
    video_url: str | None = None,
    timeout_s: float = 120.0,
    project_id: int | None = None,
) -> None:
    """Скачивание veo: physical click по 10 роликам → ID → кнопка ↓."""
    from app.services.step_cancel import abort_if_cancelled, await_with_cancel

    abort_if_cancelled(project_id)
    deadline_ms = int(timeout_s * 1000)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_thumbs = await _wait_gallery_video_thumbs(
        page, min_count=1, timeout_s=45.0, project_id=project_id
    )
    if n_thumbs < 1:
        logger.warning(
            "_download_via_video_card_click: нет video thumb за 45с (id={})",
            prompt_id_prefix,
        )

    card = None
    for c_attempt in range(1, 4):
        card = await _find_card_by_clicking_videos(
            page,
            prompt_id_prefix=prompt_id_prefix,
            limit=10,
            project_id=project_id,
        )
        if card is not None:
            break
        if c_attempt < 3:
            await asyncio.sleep(2.0)

    if card is None and video_url:
        card = await _find_card_by_img_url_click(
            page, video_url, project_id=project_id
        )

    if card is None:
        id_el = page.get_by_text(prompt_id_prefix, exact=False).first
        try:
            await await_with_cancel(
                id_el.wait_for(state="visible", timeout=5_000),
                project_id,
            )
            candidate = id_el.locator(
                "xpath=ancestor::*[descendant::button"
                "[descendant::svg[contains(@class,'lucide-download')]]][1]"
            )
            if await candidate.count() > 0:
                card = candidate
        except PWTimeoutError:
            pass

    if card is None and video_url:
        logger.warning(
            "_download_via_video_card_click: карточка не найдена, URL {}",
            video_url[:120],
        )
        await _download_via_context(
            page, video_url, out_path, project_id=project_id
        )
        return

    if card is None:
        raise OutseeImageError(
            "outsee video: не нашёл карточку с нашим ID (10 роликов перебраны)",
            context={
                "prompt_id_prefix": prompt_id_prefix,
                "video_url": video_url,
            },
        )

    with contextlib.suppress(Exception):
        await card.scroll_into_view_if_needed(timeout=5_000)
    with contextlib.suppress(Exception):
        await card.hover(timeout=5_000)

    download_btn = card.locator("button:has(svg.lucide-download)").first
    try:
        async with page.expect_download(timeout=deadline_ms) as dl_info:
            await _physical_mouse_click(
                page,
                download_btn,
                project_id=project_id,
                label="video download lucide-download",
            )
        download = await dl_info.value
        await await_with_cancel(download.save_as(str(out_path)), project_id)
    except PWTimeoutError as e:
        if video_url:
            logger.warning(
                "_download_via_video_card_click: download click timeout, URL fallback"
            )
            await _download_via_context(
                page, video_url, out_path, project_id=project_id
            )
            return
        raise OutseeImageError(
            "outsee video: клик «Скачать» не вызвал download",
            context={"prompt_id_prefix": prompt_id_prefix},
        ) from e

    logger.info(
        "_download_via_video_card_click: сохранил {} (prompt_id={})",
        out_path,
        prompt_id_prefix,
    )


async def _count_big_gallery_imgs(page: Page) -> int:
    try:
        n = await page.evaluate(
            """() => {
                let c = 0;
                for (const img of document.querySelectorAll('img')) {
                    const r = img.getBoundingClientRect();
                    if (r.width >= 180 && r.height >= 180 && img.src) c++;
                }
                return c;
            }"""
        )
        return int(n) if isinstance(n, int) else 0
    except Exception:  # noqa: BLE001
        return 0


async def _scroll_gallery_to_load_all(
    page: Page,
    *,
    project_id: int | None = None,
) -> None:
    """Scroll the gallery/main container to trigger lazy-loading of
    off-screen thumbnails.  Outsee renders the gallery in a scrollable
    container; items below the fold are lazy-loaded and invisible to
    DOM queries until they enter the viewport.  Scrolling ensures
    ``_find_card_by_clicking_images`` and ``_find_img_by_prompt_id``
    can see the freshly generated card even when the gallery is long.
    """
    from app.services.step_cancel import abort_if_cancelled

    abort_if_cancelled(project_id)
    try:
        await page.evaluate(
            """() => {
                // Try common scrollable containers first
                const scrollCandidates = [
                    ...document.querySelectorAll(
                        '[class*="gallery"], [class*="Gallery"], '
                        + '[class*="scroll"], [class*="Scroll"], '
                        + '[role="list"], [role="feed"], main'
                    ),
                    document.scrollingElement || document.documentElement,
                ];
                for (const el of scrollCandidates) {
                    if (!el) continue;
                    if (el.scrollHeight > el.clientHeight + 50) {
                        // Scroll to top (newest items in outsee are at top)
                        el.scrollTo({top: 0, behavior: 'instant'});
                    }
                }
                // Also scroll the main page to top
                window.scrollTo({top: 0, behavior: 'instant'});
            }"""
        )
        await asyncio.sleep(0.3)
        await page.evaluate(
            """() => {
                // Then scroll down a bit and back to trigger lazy loads
                const scrollEl = document.scrollingElement || document.documentElement;
                scrollEl.scrollTo({top: 500, behavior: 'instant'});
            }"""
        )
        await asyncio.sleep(0.3)
        await page.evaluate(
            """() => {
                const scrollEl = document.scrollingElement || document.documentElement;
                scrollEl.scrollTo({top: 0, behavior: 'instant'});
            }"""
        )
    except Exception:  # noqa: BLE001
        pass


async def _wait_gallery_thumbs(
    page: Page,
    *,
    min_count: int = 1,
    timeout_s: float = 45.0,
    project_id: int | None = None,
) -> int:
    """Ждём появления больших thumb'ов в галерее (после gen_idle / net_events)."""
    from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

    start = asyncio.get_event_loop().time()
    scroll_done = False
    while asyncio.get_event_loop().time() - start < timeout_s:
        abort_if_cancelled(project_id)
        n = await _count_big_gallery_imgs(page)
        if n >= min_count:
            return n
        elapsed = asyncio.get_event_loop().time() - start
        if not scroll_done and elapsed > 3.0:
            await _scroll_gallery_to_load_all(page, project_id=project_id)
            scroll_done = True
        await sleep_cancellable(1.0, project_id)
    return await _count_big_gallery_imgs(page)


async def _find_card_by_img_url_click(
    page: Page,
    img_url: str,
    *,
    project_id: int | None = None,
) -> Any | None:
    """Клик по thumb с нашим URL (без ID в панели) — как fallback для hero/frames."""
    from app.services.step_cancel import abort_if_cancelled, await_with_cancel

    abort_if_cancelled(project_id)
    url_path = _strip_url_query(img_url)
    path_only = re.sub(r"^https?://[^/]+", "", url_path)
    basename = Path(path_only).name if path_only else ""
    img_loc = None
    for fragment in (basename, path_only):
        if not fragment:
            continue
        loc = page.locator(f'img[src*="{fragment}"]').first
        if await loc.count() > 0:
            img_loc = loc
            break
    if img_loc is None:
        return None
    try:
        await _physical_mouse_click(
            page,
            img_loc,
            project_id=project_id,
            label="url-matched gallery img",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "_find_card_by_img_url_click: mouse click ({})", type(e).__name__
        )
        return None
    await asyncio.sleep(0.55)
    candidate = img_loc.locator(
        "xpath=ancestor::*[descendant::button"
        "[descendant::svg[contains(@class,'lucide-download')]]][1]"
    )
    if await candidate.count() > 0:
        logger.info(
            "_find_card_by_img_url_click: карточка по img_url ({})",
            (basename or path_only)[-48:],
        )
        return candidate
    return None


async def _find_card_by_clicking_images(
    page: Page,
    *,
    prompt_id_prefix: str,
    limit: int = 15,
    project_id: int | None = None,
):
    """Стратегия C из `_download_via_card_click`: outsee может прятать
    наш `[ID: …]` в `<textarea value="...">` или в правой панели «Промпт»,
    которая рендерится ТОЛЬКО по клику на картинку. Поэтому
    `get_by_text` его не находит.

    Алгоритм: берём первые N (по умолчанию 15) больших `<img>` в DOM
    (новые в outsee всегда добавляются сверху галереи), для каждой:
      1) скроллим в видимую часть и кликаем (открывает панель промта);
      2) ждём 600 мс что DOM обновился;
      3) собираем `body.innerText` + значения всех `<textarea>`/`<input>`
         и проверяем содержит ли любой из них токены ID
         (полный prompt_id_prefix, inner без [ID:…], 8-hex-tail);
      4) если матч — это НАША карточка. Возвращаем её ancestor
         (тот же ancestor с кнопкой download, что используется в A/B).
    """
    from app.services.step_cancel import abort_if_cancelled, await_with_cancel

    tokens = _prompt_id_search_tokens(prompt_id_prefix)

    # Scroll gallery to ensure off-screen items are loaded
    await _scroll_gallery_to_load_all(page, project_id=project_id)

    # Получаем список больших картинок (визуальный bbox >= 200x200).
    try:
        srcs: list[str] = await page.evaluate(
            """() => {
                const out = [];
                for (const img of document.querySelectorAll('img')) {
                    const r = img.getBoundingClientRect();
                    if (r.width >= 180 && r.height >= 180 && img.src) {
                        out.push(img.src);
                    }
                }
                return out;
            }"""
        )
    except Exception:  # noqa: BLE001
        return None

    if not srcs:
        return None

    logger.info(
        "_find_card_by_clicking_images: пробую перебрать {} больших картинок",
        min(len(srcs), limit),
    )

    for idx, src in enumerate(srcs[:limit]):
        abort_if_cancelled(project_id)
        stripped = _strip_url_query(src)
        path_only = re.sub(r"^https?://[^/]+", "", stripped)
        if not path_only:
            continue
        basename = Path(path_only).name
        img_loc = None
        for fragment in (basename, path_only):
            loc = page.locator(f'img[src*="{fragment}"]').first
            if await loc.count() > 0:
                img_loc = loc
                break
        if img_loc is None:
            continue

        try:
            await _physical_mouse_click(
                page,
                img_loc,
                project_id=project_id,
                label=f"gallery img #{idx}",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_find_card_by_clicking_images: mouse click img #{} упал ({})",
                idx,
                type(e).__name__,
            )
            continue

        await asyncio.sleep(0.65)

        matched = await _gallery_detail_panel_has_id(page, prompt_id_prefix)

        if not matched:
            with contextlib.suppress(Exception):
                await page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
            continue

        # Try primary card detection via download-button ancestor
        candidate = img_loc.locator(
            "xpath=ancestor::*[descendant::button"
            "[descendant::svg[contains(@class,'lucide-download')]]][1]"
        )
        if await candidate.count() > 0:
            logger.info(
                "_find_card_by_clicking_images: НАШЛА на картинке #{} "
                "(стратегия C — physical mouse + ID в панели)",
                idx,
            )
            return candidate

        # Fallback: look for any ancestor with a generic download button
        fallback_candidate = img_loc.locator(
            "xpath=ancestor::*[descendant::button[contains(@class,'download') "
            "or contains(@aria-label,'download') or contains(@aria-label,'Download') "
            "or contains(@aria-label,'Скачать')]][1]"
        )
        if await fallback_candidate.count() > 0:
            logger.info(
                "_find_card_by_clicking_images: НАШЛА на картинке #{} "
                "(стратегия C — fallback download button)",
                idx,
            )
            return fallback_candidate

        with contextlib.suppress(Exception):
            await page.keyboard.press("Escape")

    logger.warning(
        "_find_card_by_clicking_images: перебрал {} картинок, нашей не нашлось",
        min(len(srcs), limit),
    )
    return None


async def _download_via_card_click(
    page: Page,
    *,
    prompt_id_prefix: str,
    out_path: Path,
    timeout_s: float = 120.0,
    project_id: int | None = None,
    img_url: str | None = None,
) -> None:
    """Кликает зелёную «↓ Скачать» на карточке результата с нашим
    `[ID: P{...}-F{...}-{8hex}]` и сохраняет реальный финальный файл
    через `page.expect_download()`.

    Преимущество перед старым `_download_via_context(page, img_url, ...)`:
    мы НЕ извлекаем URL из `<img src>` — outsee часто кладёт туда
    плейсхолдер (например `topaz.webp` пока работает upscale, или
    `input_*.png` — ссылку на наш же референс). Реальный финальный
    PNG/JPEG отдаётся ТОЛЬКО при клике по кнопке «Download».

    Стратегии (как в TG-боте / hero): ждём thumbs → C → D → B → A → URL-fallback.
    """
    from app.services.step_cancel import abort_if_cancelled, await_with_cancel

    abort_if_cancelled(project_id)
    deadline_ms = int(timeout_s * 1000)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    card = None  # type: ignore[var-annotated]

    # Check for visible outsee error banners before attempting download.
    # A banner in the result panel may indicate the generation failed
    # even though _wait_image_url_strict returned a URL.
    try:
        err_text = await page.evaluate(
            """() => {
                const markers = ['ошибка', 'контент отклон', 'content reject',
                    'failed', 'не удалось', 'something went wrong'];
                for (const el of document.querySelectorAll(
                    '[class*="error"], [class*="Error"], '
                    + '[class*="alert"], [class*="Alert"], '
                    + '[role="alert"]'
                )) {
                    const t = (el.innerText || '').toLowerCase().trim();
                    if (t.length < 5 || t.length > 500) continue;
                    for (const m of markers) {
                        if (t.includes(m)) return t.substring(0, 200);
                    }
                }
                return null;
            }"""
        )
        if err_text:
            logger.warning(
                "_download_via_card_click: error banner before download: {}",
                err_text[:120],
            )
    except Exception:  # noqa: BLE001
        pass

    # Галерея часто появляется позже CDN-URL — ждём thumbs (hero и frames).
    n_thumbs = await _wait_gallery_thumbs(
        page, min_count=1, timeout_s=45.0, project_id=project_id
    )
    if n_thumbs < 1:
        logger.warning(
            "_download_via_card_click: в галерее нет больших thumb за 45с, "
            "всё равно пробую клики (id={})",
            prompt_id_prefix,
        )

    # --- C: перебор 15 картинок + ID в панели (основная логика бота).
    for c_attempt in range(1, 6):
        card = await _find_card_by_clicking_images(
            page,
            prompt_id_prefix=prompt_id_prefix,
            limit=15,
            project_id=project_id,
        )
        if card is not None:
            break
        if c_attempt < 5:
            await asyncio.sleep(2.0)
            if c_attempt >= 2:
                await _scroll_gallery_to_load_all(
                    page, project_id=project_id
                )

    # --- D: физический клик по img_url из wait (без ID в панели).
    if card is None and img_url:
        card = await _find_card_by_img_url_click(
            page, img_url, project_id=project_id
        )

    # --- B: get_by_text([ID: …]).
    if card is None:
        id_el = page.get_by_text(prompt_id_prefix, exact=False).first
        try:
            await await_with_cancel(
                id_el.wait_for(state="visible", timeout=5_000),
                project_id,
            )
            candidate = id_el.locator(
                "xpath=ancestor::*[descendant::button"
                "[descendant::svg[contains(@class,'lucide-download')]]][1]"
            )
            if await candidate.count() > 0:
                card = candidate
                logger.info(
                    "_download_via_card_click: карточка найдена "
                    "через get_by_text (стратегия B)"
                )
        except PWTimeoutError:
            logger.warning(
                "_download_via_card_click: стратегия B не сработала"
            )

    # --- A: якорь по img_url из wait.
    if card is None and img_url:
        url_path = _strip_url_query(img_url)
        path_only = re.sub(r"^https?://[^/]+", "", url_path)
        basename = Path(path_only).name if path_only else ""
        for fragment in (basename, path_only):
            if not fragment:
                continue
            try:
                img_locator = page.locator(
                    f'img[src*="{fragment}"]'
                ).first
                await await_with_cancel(
                    img_locator.wait_for(state="attached", timeout=5_000),
                    project_id,
                )
                candidate = img_locator.locator(
                    "xpath=ancestor::*[descendant::button"
                    "[descendant::svg[contains(@class,'lucide-download')]]][1]"
                )
                if await candidate.count() > 0:
                    card = candidate
                    logger.info(
                        "_download_via_card_click: карточка найдена "
                        "через img_url (стратегия A) {}",
                        fragment[-50:],
                    )
                    break
            except (PWTimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(
                    "_download_via_card_click: стратегия A '{}' ({})",
                    fragment[-40:], type(e).__name__,
                )

    # --- E (last resort): reload page and retry strategy C once.
    # Outsee page state can become corrupted after long generation;
    # a fresh page load re-renders the gallery with all items.
    if card is None:
        logger.info(
            "_download_via_card_click: все стратегии B/C/D/A провалились — "
            "перезагружаю страницу и пробую стратегию C ещё раз"
        )
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15_000)
            await asyncio.sleep(2.0)
            n_thumbs2 = await _wait_gallery_thumbs(
                page, min_count=1, timeout_s=20.0, project_id=project_id
            )
            if n_thumbs2 >= 1:
                card = await _find_card_by_clicking_images(
                    page,
                    prompt_id_prefix=prompt_id_prefix,
                    limit=15,
                    project_id=project_id,
                )
                if card is not None:
                    logger.info(
                        "_download_via_card_click: стратегия E (reload+C) сработала!"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_download_via_card_click: стратегия E (reload) упала: {}",
                e,
            )

    if card is None and img_url:
        logger.warning(
            "_download_via_card_click: клик по карточке не удался, "
            "скачиваю по URL {}",
            img_url[:120],
        )
        await _download_via_context(
            page, img_url, out_path, project_id=project_id,
        )
        logger.info(
            "_download_via_card_click: сохранил {} (URL-fallback, id={})",
            out_path, prompt_id_prefix,
        )
        return

    if card is None:
        raise OutseeImageError(
            "outsee image: не нашёл карточку с нашим ID "
            "(скачивание по клику невозможно, URL нет)",
            context={
                "prompt_id_prefix": prompt_id_prefix,
                "img_url": img_url,
                "timeout_s": timeout_s,
            },
        )

    # Не критично — карточка может быть и так в видимой области.
    with contextlib.suppress(Exception):
        await card.scroll_into_view_if_needed(timeout=5_000)

    # 3) Кнопки действий (download/heart/regen/trash) появляются только
    #    при hover на карточку — без этого click может не зарегаться,
    #    т.к. кнопка не actionable (opacity:0/display:none по CSS).
    try:
        await card.hover(timeout=5_000)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "_download_via_card_click: hover упал ({}), всё равно "
            "пробуем кликнуть — Playwright auto-wait может обработать",
            type(e).__name__,
        )

    # 4) Внутри карточки находим именно кнопку с lucide-download SVG.
    #    Эта же иконка живёт в библиотеке lucide-icons — её класс
    #    `lucide-download` стабилен и не зависит от Tailwind-стилей.
    download_btn = card.locator("button:has(svg.lucide-download)").first

    try:
        async with page.expect_download(timeout=deadline_ms) as dl_info:
            await _physical_mouse_click(
                page,
                download_btn,
                project_id=project_id,
                label="download lucide-download",
            )
        download = await dl_info.value
        await await_with_cancel(download.save_as(str(out_path)), project_id)
    except PWTimeoutError as e:
        raise OutseeImageError(
            "outsee image: клик по кнопке «Скачать» не вызвал download "
            "за отведённое время",
            context={
                "prompt_id_prefix": prompt_id_prefix,
                "timeout_s": timeout_s,
                "err": f"{type(e).__name__}: {e}",
            },
        ) from e
    except Exception as e:  # noqa: BLE001
        raise OutseeImageError(
            "outsee image: download через клик по карточке упал",
            context={
                "prompt_id_prefix": prompt_id_prefix,
                "err": f"{type(e).__name__}: {e}",
            },
        ) from e

    logger.info(
        "_download_via_card_click: сохранил файл {} (prompt_id={})",
        out_path, prompt_id_prefix,
    )


async def _download_via_context(
    page: Page,
    url: str,
    out_path: Path,
    *,
    timeout_ms: int = 120_000,
    attempts: int = 3,
    project_id: int | None = None,
) -> None:
    """Скачивает файл по URL, используя тот же контекст (cookies/auth) страницы.
    CDN outsee/hailuoai иногда медленный — поднимаем таймаут до 120 сек и
    делаем до 3 попыток."""
    from app.services.step_cancel import abort_if_cancelled, await_with_cancel, sleep_cancellable

    ctx = page.context
    api = ctx.request
    last: Exception | None = None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(1, attempts + 1):
        abort_if_cancelled(project_id)
        try:
            resp = await await_with_cancel(api.get(url, timeout=timeout_ms), project_id)
            if resp.status >= 400:
                raise RuntimeError(f"download {url} failed: HTTP {resp.status}")
            # Pre-save content checks: verify we got an image, not HTML
            ct = (resp.headers.get("content-type") or "").lower()
            if ct and not ct.startswith("image/") and "octet-stream" not in ct:
                logger.warning(
                    "_download_via_context: Content-Type={} — не image, "
                    "пропускаю попытку {}/{}",
                    ct, i, attempts,
                )
                raise RuntimeError(
                    f"download {url}: unexpected Content-Type {ct}"
                )
            body = await await_with_cancel(resp.body(), project_id)
            # Magic byte check before writing to disk
            if len(body) >= 16:
                is_png = body[:8] == _PNG_MAGIC
                is_jpeg = body[:3] == _JPEG_MAGIC
                is_webp = body[:4] == _RIFF_MAGIC and body[8:12] == _WEBP_TAG
                if not (is_png or is_jpeg or is_webp):
                    logger.warning(
                        "_download_via_context: magic bytes не PNG/JPEG/WebP "
                        "(head={}), попытка {}/{}",
                        body[:16].hex(), i, attempts,
                    )
                    if i < attempts:
                        raise RuntimeError(
                            f"download {url}: bad magic bytes"
                        )
            if len(body) < _MIN_IMAGE_BYTES:
                logger.warning(
                    "_download_via_context: тело {} байт < {} мин, "
                    "попытка {}/{}",
                    len(body), _MIN_IMAGE_BYTES, i, attempts,
                )
                if i < attempts:
                    raise RuntimeError(
                        f"download {url}: too small ({len(body)} bytes)"
                    )
            out_path.write_bytes(body)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            logger.warning(
                "_download_via_context: попытка {}/{} упала: {}: {}",
                i,
                attempts,
                type(e).__name__,
                e,
            )
            await sleep_cancellable(1.5 * i, project_id)
    assert last is not None
    raise last


# ---------- recon util: python -m app.bots.outsee recon-image "prompt" ----------


async def _recon_generate_buttons(kind: str = "video") -> None:
    """Скан кнопок Generate на открытой в Chrome странице outsee.

    Запускай ПОСЛЕ логина в outsee в том же Chrome (CDP :29229).
    Пишет в data/outsee_dumps/: JSON с координатами, PNG, HTML.
  """
    import json
    from datetime import datetime as _dt

    url = settings.outsee_image_url if kind == "image" else settings.outsee_video_url
    dumps_dir = Path(settings.data_dir) / "outsee_dumps"
    dumps_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.utcnow().strftime("%Y%m%d_%H%M%S")
    label = f"recon_generate_{kind}_{ts}"

    async with browser_session() as bs:
        page = await bs.open_page(url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(3)

        scan = await page.evaluate(
            """() => {
                const keywords = [
                    'генерир', 'создать', 'generate', 'run', 'генерация'
                ];
                const visible = (el) => {
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 4 && r.height > 4;
                };
                const allButtons = [];
                for (const btn of document.querySelectorAll('button, [role="button"]')) {
                    const text = (btn.innerText || btn.textContent || '').trim();
                    const low = text.toLowerCase();
                    const hit = keywords.some(k => low.includes(k));
                    if (!hit && !btn.getAttribute('data-testid')) continue;
                    const r = btn.getBoundingClientRect();
                    allButtons.push({
                        tag: btn.tagName,
                        text: text.slice(0, 80),
                        disabled: !!btn.disabled,
                        ariaDisabled: btn.getAttribute('aria-disabled'),
                        dataTestid: btn.getAttribute('data-testid'),
                        type: btn.getAttribute('type'),
                        className: (btn.className && btn.className.toString().slice(0, 100)) || '',
                        visible: visible(btn),
                        x: Math.round(r.x), y: Math.round(r.y),
                        w: Math.round(r.width), h: Math.round(r.height),
                        cx: Math.round(r.x + r.width / 2),
                        cy: Math.round(r.y + r.height / 2),
                    });
                }
                return allButtons;
            }"""
        )

        selector_hits: list[dict[str, Any]] = []
        for sel in GENERATE_BUTTON_SELECTORS:
            hit: dict[str, Any] = {"selector": sel, "count": 0, "visible_nth": []}
            try:
                loc = page.locator(sel)
                count = await loc.count()
                hit["count"] = count
                for i in range(min(count, 5)):
                    nth = loc.nth(i)
                    try:
                        vis = await nth.is_visible()
                        dis = await nth.is_disabled()
                        box = await nth.bounding_box()
                        hit["visible_nth"].append({
                            "i": i,
                            "visible": vis,
                            "disabled": dis,
                            "box": box,
                        })
                    except Exception as e:  # noqa: BLE001
                        hit["visible_nth"].append({"i": i, "err": str(e)})
            except Exception as e:  # noqa: BLE001
                hit["error"] = str(e)
            selector_hits.append(hit)

        first_sel = await _first_visible(
            page, GENERATE_BUTTON_SELECTORS, timeout_ms=5_000
        )
        report = {
            "kind": kind,
            "url": page.url,
            "first_visible_selector": first_sel,
            "dom_generate_like_buttons": scan or [],
            "configured_selectors": selector_hits,
        }
        json_path = dumps_dir / f"{label}.json"
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        png_path = dumps_dir / f"{label}.png"
        html_path = dumps_dir / f"{label}.html"
        try:
            await page.screenshot(path=str(png_path), full_page=True, timeout=15_000)
        except Exception as e:  # noqa: BLE001
            logger.warning("recon screenshot failed: {}", e)
            png_path = None
        try:
            html_path.write_text(await page.content(), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("recon html failed: {}", e)
            html_path = None

        logger.info("=== recon-generate {} ===", kind)
        logger.info("page url: {}", page.url)
        logger.info("first_visible GENERATE selector: {}", first_sel)
        logger.info("DOM buttons (generate-like): {}", len(scan or []))
        for i, b in enumerate(scan or []):
            logger.info(
                "  [{}] visible={} disabled={} ({},{}) {}×{} text={!r}",
                i,
                b.get("visible"),
                b.get("disabled"),
                b.get("cx"),
                b.get("cy"),
                b.get("w"),
                b.get("h"),
                b.get("text"),
            )
        logger.info("JSON → {}", json_path)
        if png_path:
            logger.info("PNG  → {}", png_path)
        if html_path:
            logger.info("HTML → {}", html_path)
        print(f"\nГотово. Открой файл и пришли в чат:\n  {json_path}")
        if png_path:
            print(f"  {png_path}")


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
    if len(sys.argv) < 2:
        print(
            "usage:\n"
            "  python -m app.bots.outsee recon-generate [video|image]\n"
            "  python -m app.bots.outsee recon-video <prompt> [start_frame]\n"
            "  python -m app.bots.outsee recon-image <prompt>"
        )
        sys.exit(1)
    cmd = sys.argv[1].lower()
    if cmd.startswith("recon-generate"):
        kind = "video"
        if len(sys.argv) > 2 and "image" in sys.argv[2].lower():
            kind = "image"
        asyncio.run(_recon_generate_buttons(kind))
        return
    if len(sys.argv) < 3:
        print("usage: python -m app.bots.outsee recon-image|recon-video <prompt> [start_frame]")
        sys.exit(1)
    prompt = sys.argv[2]
    start = sys.argv[3] if len(sys.argv) > 3 else None
    kind = "image" if "image" in cmd else "video"
    asyncio.run(_recon(kind, prompt, start))


if __name__ == "__main__":
    _cli()
