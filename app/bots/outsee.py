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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PWTimeoutError

from app.bots.browser import BrowserSession, browser_session
from app.generation_options import VIDEO_GENERATORS
from app.settings import settings

# ----- Справочник outsee_slug → display_name для UI-выбора модели -----
# Используется в `_select_video_model_via_button` чтобы клик'нуть нужную
# карточку в модалке «Поменять» после открытия страницы.
VIDEO_MODEL_DISPLAY_BY_SLUG: dict[str, str] = {
    c.outsee_slug: c.label for c in VIDEO_GENERATORS if c.outsee_slug
}

# ----- Принудительный реремап slug'ов на этапе вызова outsee -----
# По требованию юзера: Veo 3 Fast и Veo 3.1 Fast → всегда Veo 3.1 Lite.
# (В настройках проекта остаётся исходная модель — для xlsx/GPT-контекста —
# но в outsee.io бот выбирает Lite. Если нужно расширить — добавить сюда.)
VIDEO_MODEL_SLUG_REMAP: dict[str, str] = {
    "veo-3-fast": "veo-3-1-lite",
    "veo-3-1-fast": "veo-3-1-lite",
}


def _remap_video_slug(slug: str | None) -> str | None:
    """Применяет VIDEO_MODEL_SLUG_REMAP. Возвращает исходный slug если
    маппинга нет."""
    if not slug:
        return slug
    return VIDEO_MODEL_SLUG_REMAP.get(slug, slug)

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
    # САМЫЙ СТРОГИЙ: text-is исключает «Перегенерировать» /
    # «Что генерируется?» и прочие substring-ловушки. Без
    # `:not([disabled])` в первых вариантах — карточка может быть
    # disabled пока не вставлен промт, но после разблокируется.
    "button:text-is('Генерировать'):not([disabled])",
    "button:text-is('Сгенерировать'):not([disabled])",
    "button:text-is('Создать'):not([disabled])",
    "button:text-is('Generate'):not([disabled])",
    "button:text-is('Генерировать')",
    "button:text-is('Сгенерировать')",
    "button:text-is('Создать')",
    "button:text-is('Generate')",
    # Фаллбэки — has-text (substring): хватают «Перегенерировать»
    # и т.п., но это всё равно триггер генерации — крайний случай.
    "button:has-text('Генерировать'):not([disabled])",
    "button:has-text('Сгенерировать'):not([disabled])",
    "button:has-text('Генерировать')",
    "button:has-text('Сгенерировать')",
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
# В outsee.io 2026 в классическом дизайне это checkbox с текстом
# «Relax Режим» (под надписью «Дешевле, но может генерировать дольше
# обычного»). В старых выкатках это могло называться просто «Relax»
# или тогл «Безлимит». Покрываем все три случая.
RELAX_SELECTORS: list[str] = [
    # Новый UI: чекбокс с текстом «Relax Режим».
    "label:has-text('Relax Режим')",
    "button:has-text('Relax Режим')",
    "[role='checkbox']:near(:text-is('Relax Режим'))",
    "div:has(> :text-is('Relax Режим')) input[type='checkbox']",
    "*:has(> :text-is('Relax Режим'))",
    # Старые варианты.
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
    max_attempts: int = 3,
) -> bool:
    """Ставит тогл Relax в нужное состояние и ВЕРИФИЦИРУЕТ что состояние
    реально сменилось. При несовпадении — повторяет физический CDP-клик
    до `max_attempts` раз.

    На outsee.io 2026 года тогл называется «Безлимит» — и это И ЕСТЬ
    Relax-режим (юзер подтвердил):
      Relax=ON  ⇔ Безлимит=ON
      Relax=OFF ⇔ Безлимит=OFF

    Зачем физ.клик + верификация:
      Раньше клик делался синтетический (`locator.click()`). Если форма
      ещё не закончила гидрацию React'а, synthetic-click уходит впустую:
      внешне всё ок (нет исключения), но `bg-primary` на тогле не
      появляется, и следующий шаг `generate` стартует со старым Relax-
      состоянием. Юзер видит «кнопка релакс не нажалась → Generate
      слишком рано».

      Решение: использовать `_physical_click_element` (Input.dispatch
      MouseEvent через CDP, isTrusted=true) и после клика
      перечитывать состояние через `_read_limit_toggle_on`. Если за
      `settle_s` секунд состояние не сменилось — повторить.

    Возвращает True если итоговое состояние совпало с want_on (или
    толла нет на странице и юзер просил want_on=False), иначе False.
    Если want_on=True а тогла нет — дампим страницу для отладки и
    возвращаем False.
    """
    settle_s: float = 0.7  # сколько ждать после клика прежде чем перечитать
    desired_label = "ON" if want_on else "OFF"

    # 1) Сначала пробуем NEW UI: «Безлимит».
    limit_sel = await _first_visible(
        page, LIMIT_TOGGLE_SELECTORS, timeout_ms=1_500
    )
    if limit_sel:
        try:
            current_on = await _read_limit_toggle_on(page, limit_sel)
            if current_on is want_on:
                logger.info(
                    "outsee.{}: Relax {} — Безлимит уже в нужном состоянии "
                    "(тогл не трогаем)",
                    where, desired_label,
                )
                return True
            for attempt in range(1, max_attempts + 1):
                ok = await _physical_click_element(
                    page, limit_sel,
                    description=f"{where}:Безлимит(att{attempt})",
                    timeout_s=3.0,
                )
                logger.info(
                    "outsee.{}: физ.клик по тогле «Безлимит» #{}/{} "
                    "(want={}, before={}, dispatched={})",
                    where, attempt, max_attempts, desired_label,
                    current_on, ok,
                )
                # Ждём пока React перерисует, потом перечитаем.
                await asyncio.sleep(settle_s)
                after_on = await _read_limit_toggle_on(page, limit_sel)
                logger.info(
                    "outsee.{}: после клика «Безлимит» состояние = {} "
                    "(хочу {})", where, after_on, desired_label,
                )
                if after_on is want_on:
                    logger.info(
                        "outsee.{}: Relax {} подтверждён (попыток: {})",
                        where, desired_label, attempt,
                    )
                    return True
                # Иначе — ещё одна попытка (state мог не успеть, либо
                # клик прошёл мимо). Пауза между попытками.
                await asyncio.sleep(0.5)
                current_on = after_on
            logger.warning(
                "outsee.{}: Relax {} НЕ подтверждён после {} попыток "
                "(финальное состояние={}) — дампим страницу",
                where, desired_label, max_attempts, current_on,
            )
            try:
                h, p = await _dump_page(page, f"relax_not_confirmed_{where}")
                if dumps is not None:
                    for x in (h, p):
                        if x:
                            dumps.append(x)
            except Exception:  # noqa: BLE001
                pass
            return False
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
            return False
        return True
    try:
        async def _read_aria_state() -> bool | None:
            loc = page.locator(sel).first
            for attr in ("aria-checked", "aria-pressed", "data-state"):
                try:
                    v = await loc.get_attribute(attr, timeout=500)
                    if v is not None:
                        s = str(v).lower()
                        if s in ("true", "on", "checked"):
                            return True
                        if s in ("false", "off", "unchecked"):
                            return False
                except Exception:  # noqa: BLE001
                    continue
            return None

        is_on = await _read_aria_state()
        if want_on and is_on is True:
            logger.info("outsee.{}: Relax уже включён, пропускаем клик", where)
            return True
        if not want_on and is_on is False:
            logger.info("outsee.{}: Relax уже выключен, пропускаем клик", where)
            return True
        if not want_on and is_on is None:
            logger.info(
                "outsee.{}: Relax=off запрошен, но состояние неизвестно — "
                "не трогаем", where,
            )
            return True
        for attempt in range(1, max_attempts + 1):
            ok = await _physical_click_element(
                page, sel,
                description=f"{where}:RelaxFallback(att{attempt})",
                timeout_s=3.0,
            )
            logger.info(
                "outsee.{}: физ.клик по «Relax» #{}/{} (sel={}, want={}, "
                "dispatched={})",
                where, attempt, max_attempts, sel, desired_label, ok,
            )
            await asyncio.sleep(settle_s)
            after = await _read_aria_state()
            logger.info(
                "outsee.{}: после клика «Relax» state={} (хочу {})",
                where, after, desired_label,
            )
            if after is want_on:
                logger.info(
                    "outsee.{}: Relax {} подтверждён (попыток: {})",
                    where, desired_label, attempt,
                )
                return True
            await asyncio.sleep(0.5)
        logger.warning(
            "outsee.{}: Relax {} НЕ подтверждён через RELAX_SELECTORS "
            "после {} попыток", where, desired_label, max_attempts,
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("outsee.{}: Relax toggle упал: {}", where, e)
        return False


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
    # Применяем VIDEO_MODEL_SLUG_REMAP — чтобы и в URL, и в UI отображалась
    # одна и та же модель (например Veo 3.1 Fast → Veo 3.1 Lite).
    model_slug = _remap_video_slug(model_slug)
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

# ----- Селекторы кнопки выбора модели (новый UI 2026, «Классика») -----
# В левой панели «Настройки» есть отдельная кнопка-селектор модели вида:
#   <button>
#     <img src="/videomobilepreview/<model>.webp">
#     <span>Модель</span><span>Seedance Pro 1.5</span>
#     <svg chevron-right>
#   </button>
# ВАЖНО: кнопка «Поменять» наверху, рядом с превьюшкой стиля, открывает
# модалку «Выбор визуального стиля» (стили: Свободный/Взрыв/Аутфит), а
# НЕ переключает модель. Для модели нужна именно эта кнопка ниже.
MODEL_CHANGE_BUTTON_SELECTORS: list[str] = [
    # Самое надёжное: кнопка содержит подпись «Модель» в span внутри.
    "button:has(span:text-is('Модель'))",
    # Запасной — span с «Модель» как подпись.
    "button:has(span:has-text('Модель'))",
    "[role='button']:has(span:text-is('Модель'))",
]

# Текущее имя модели в карточке (используем чтобы понять — уже стоит нужная
# или нет). Структура: <button>...<span>Модель</span><span>X</span>...</button>.
CURRENT_MODEL_NAME_SELECTORS: list[str] = [
    # Прицельно: <span>X</span> ИДУЩИЙ ПОСЛЕ <span>Модель</span> внутри кнопки.
    "button:has(span:text-is('Модель')) span.text-xs.font-semibold",
    # Запасные — структура та же, только классы могут отличаться.
    "button:has(span:text-is('Модель')) span.font-semibold",
    "button:has(span:has-text('Модель')) span:not(:text-is('Модель'))",
]


def _video_model_option_selectors(display_name: str) -> list[str]:
    """Селекторы пункта выбора модели в открывшейся модалке.

    Модалка outsee.io не задокументирована — пробуем самые вероятные
    паттерны: явные роли (option/menuitem/button) + текст-метка модели.
    """
    return [
        f"[role='option']:has-text('{display_name}')",
        f"[role='menuitem']:has-text('{display_name}')",
        f"button:has-text('{display_name}')",
        f"li:has-text('{display_name}')",
        f"div[role='button']:has-text('{display_name}')",
        # Cards в модалке: <div ...>{name}</div> + что-то кликабельное вокруг.
        f"*:has(> :text-is('{display_name}'))",
    ]


async def _select_video_model_via_button(
    page: Any, slug: str | None, *, dumps: list[Path] | None = None,
) -> bool:
    """Выбирает нужную видео-модель через клик по «Поменять» на странице
    outsee.io/video. Возвращает True если кнопка отжалась И селект
    модели прошёл (или модель уже была выбрана), False если что-то
    пошло не так (тогда полагаемся на ?model=… в URL).

    Идея: страница могла открыться по URL `?model=<slug>`, и при этом
    реально модель уже стоит правильная. Но если URL-параметр не
    отработал (или юзер хочет «руками» переключиться), бот должен
    явно кликнуть «Поменять» и выбрать модель в модалке.

    Алгоритм:
      1) Если slug пустой/неизвестный — пропускаем.
      2) Читаем имя модели из карточки. Если уже совпадает с нашим
         display_name — return True (ничего не трогаем).
      3) Клик «Поменять» → ждём модалку.
      4) Клик по пункту с display_name.
      5) Если есть кнопка подтверждения «Применить»/«Выбрать»/«ОК» —
         жмём её. Иначе модалка закрывается сама.
    """
    if not slug:
        return True
    # Применяем реремап (Veo 3 Fast / Veo 3.1 Fast → Veo 3.1 Lite и т.п.).
    remapped = _remap_video_slug(slug)
    if remapped != slug:
        logger.info(
            "outsee.select_model: slug '{}' переремапнут на '{}' "
            "(см. VIDEO_MODEL_SLUG_REMAP)", slug, remapped,
        )
        slug = remapped
    display_name = VIDEO_MODEL_DISPLAY_BY_SLUG.get(slug)
    if not display_name:
        logger.warning(
            "outsee.select_model: slug={} нет в "
            "VIDEO_MODEL_DISPLAY_BY_SLUG — пропускаю UI-выбор", slug,
        )
        return True

    # 1) Проверяем, не стоит ли уже нужная модель.
    try:
        for cur_sel in CURRENT_MODEL_NAME_SELECTORS:
            try:
                loc = page.locator(cur_sel).first
                if (await loc.count()) <= 0:
                    continue
                cur = (await loc.inner_text(timeout=1_000)) or ""
                cur = cur.strip()
                if cur and cur.lower() == display_name.lower():
                    logger.info(
                        "outsee.select_model: модель '{}' уже выбрана "
                        "(не кликаю «Поменять»)", display_name,
                    )
                    return True
                if cur:
                    logger.info(
                        "outsee.select_model: текущая модель '{}' "
                        "(хочу '{}'), жму «Поменять»", cur, display_name,
                    )
                    break
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    # 2) Клик по «Поменять».
    change_sel = await _first_visible(
        page, MODEL_CHANGE_BUTTON_SELECTORS, timeout_ms=3_000
    )
    if not change_sel:
        logger.warning(
            "outsee.select_model: кнопка «Поменять» не найдена — "
            "полагаюсь на ?model=… в URL",
        )
        h, p = await _dump_page(page, "model_change_button_notfound")
        if dumps is not None:
            for x in (h, p):
                if x:
                    dumps.append(x)
        return False
    try:
        await page.locator(change_sel).first.click(timeout=3_000)
        logger.info(
            "outsee.select_model: «Поменять» нажато ({}) — жду модалку",
            change_sel,
        )
        await asyncio.sleep(0.5)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "outsee.select_model: клик по «Поменять» упал: {}", e,
        )
        return False

    # 3) Выбор пункта с display_name.
    opt_sel = await _first_visible(
        page, _video_model_option_selectors(display_name), timeout_ms=5_000
    )
    if not opt_sel:
        logger.warning(
            "outsee.select_model: пункт '{}' в модалке не найден",
            display_name,
        )
        h, p = await _dump_page(
            page, f"model_option_{slug}_notfound",
        )
        if dumps is not None:
            for x in (h, p):
                if x:
                    dumps.append(x)
        # Пытаемся закрыть модалку Escape.
        with contextlib.suppress(Exception):
            await page.keyboard.press("Escape")
        return False
    try:
        await page.locator(opt_sel).first.click(timeout=3_000)
        logger.info(
            "outsee.select_model: модель '{}' выбрана в модалке ({})",
            display_name, opt_sel,
        )
        await asyncio.sleep(0.5)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "outsee.select_model: клик по '{}' упал: {} ({})",
            display_name, e, opt_sel,
        )
        return False

    # 4) Подтверждение (если есть кнопка «Применить»/«Выбрать»).
    confirm_selectors = [
        "button:has-text('Применить'):not([disabled])",
        "button:has-text('Выбрать'):not([disabled])",
        "button:has-text('OK'):not([disabled])",
        "button:has-text('ОК'):not([disabled])",
        "button:has-text('Подтвердить'):not([disabled])",
    ]
    confirm_sel = await _first_visible(
        page, confirm_selectors, timeout_ms=800
    )
    if confirm_sel:
        try:
            await page.locator(confirm_sel).first.click(timeout=2_000)
            logger.info(
                "outsee.select_model: confirm ({})", confirm_sel,
            )
            await asyncio.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.select_model: confirm-клик упал: {}", e,
            )
    return True

# Селекторы для конкретно «Первый кадр» (новый UI outsee.io 2026).
# В классическом дизайне VIDEO-страницы есть две карточки загрузки:
#   - «Первый кадр (опционально)»   ← нам сюда
#   - «Последний кадр (опционально)»
# Каждая карточка содержит свой скрытый input[type=file]. Раньше код
# брал просто `input[type=file]` last — а это «Последний кадр». Неверно.
# Теперь ищем именно по тексту-метке «Первый кадр».
FIRST_FRAME_CARD_SELECTORS: list[str] = [
    "label:has-text('Первый кадр')",
    "button:has-text('Первый кадр')",
    "div:has(> :text-is('Первый кадр'))",
    "*:has(> :text-is('Первый кадр'))",
]
FIRST_FRAME_INPUT_SELECTORS: list[str] = [
    "label:has-text('Первый кадр') input[type='file']",
    "button:has-text('Первый кадр') input[type='file']",
    "div:has(:text-is('Первый кадр')) input[type='file']",
    "*:has(:text-is('Первый кадр')) input[type='file']",
]
LAST_FRAME_CARD_SELECTORS: list[str] = [
    "label:has-text('Последний кадр')",
    "button:has-text('Последний кадр')",
    "div:has(> :text-is('Последний кадр'))",
]
LAST_FRAME_INPUT_SELECTORS: list[str] = [
    "label:has-text('Последний кадр') input[type='file']",
    "div:has(:text-is('Последний кадр')) input[type='file']",
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

    @property
    def success(self) -> bool:
        """True если файл реально сохранён на диск.

        Неудачные генерации обычно кидают OutseeImageError, но
        вызывающий код может проверить этот флаг на всякий случай.
        """
        try:
            return self.file_path.exists() and self.file_path.stat().st_size > 0
        except Exception:  # noqa: BLE001
            return False


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


def _is_candidate_video_response(resp: Any) -> bool:
    """Подходит ли сетевой ответ под «вероятно, это mp4 нашего нового
    видео-результата»:
      - URL содержит `.mp4` (case-insensitive),
      - URL не data:/blob:,
      - URL c CDN outsee/yandex (отсекаем UI-ассеты типа .next/static).

    Используется в `generate_video` для сбора `net_events` — реальных
    подписанных URL'ов mp4, прилетающих в браузер после клика Generate.
    Подмена URL через thumb→mp4 не работает (X-Amz-Signature привязана
    к пути thumb, прямой GET .mp4-варианта = HTTP 403), поэтому
    сеть-listener — единственный надёжный источник реальных mp4-URL'ов
    помимо клика по thumb и `<video>.src` в открывшемся lightbox.
    """
    try:
        url = (resp.url or "").lower()
        if not url:
            return False
        if url.startswith("data:") or url.startswith("blob:"):
            return False
        if ".mp4" not in url:
            return False
        # Только CDN outsee/yandex. UI-ассеты (next/static/chunks/*.mp4 на
        # маркетинговой странице) не нужны.
        if (
            "storage.yandexcloud" not in url
            and "outsee" not in url
            and "cdn" not in url
        ):
            return False
        return True
    except Exception:  # noqa: BLE001
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


async def _physical_click_element(
    page: Page,
    selector: str,
    *,
    description: str = "button",
    timeout_s: float = 5.0,
    fallback_locator_click: bool = True,
) -> bool:
    """Кликает по элементу ФИЗИЧЕСКОЙ мышью через CDP, а не через
    Playwright `locator.click()`.

    Зачем: outsee.io в ряде случаев игнорирует Playwright-click
    (особенно по кнопке Generate в момент сразу после `settings.json`
    — форма ещё «дорисовывается», и синтетический click уходит в
    пустоту: `net_events=0` и DOM не меняется). Реальная мышь —
    `Input.dispatchMouseEvent` с `isTrusted=true` — проходит
    hit-test/обработчики так же как живой пользователь.

    Алгоритм:
      1) `locator.scroll_into_view_if_needed` — гарантирует видимость;
      2) `getBoundingClientRect` через JS → центр (cx, cy) внутри
         viewport;
      3) `page.mouse.move(cx, cy)` + `page.mouse.click(cx, cy, delay=50)`.

    Если шаг 2/3 упал, и `fallback_locator_click=True` — фолбэк на
    обычный `locator.click()` (чтобы не рушить пайплайн, если bbox
    вышел из viewport или element_handle устарел).

    Возвращает True если физический CDP-клик выполнен, False если
    был использован фолбэк или элемент недоступен.
    """
    try:
        loc = page.locator(selector).first
        try:
            await loc.scroll_into_view_if_needed(
                timeout=int(timeout_s * 1000)
            )
        except Exception:  # noqa: BLE001
            pass
        handle = await loc.element_handle()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "_physical_click_element[{}]: locator упал ({}): {}",
            description, selector, e,
        )
        if fallback_locator_click:
            try:
                await page.locator(selector).first.click()
            except Exception:  # noqa: BLE001
                pass
        return False
    if handle is None:
        logger.warning(
            "_physical_click_element[{}]: element_handle=None ({})",
            description, selector,
        )
        if fallback_locator_click:
            try:
                await page.locator(selector).first.click()
            except Exception:  # noqa: BLE001
                pass
        return False
    try:
        rect = await page.evaluate(
            """(el) => {
                const r = el.getBoundingClientRect();
                return {
                    cx: Math.round(r.left + r.width / 2),
                    cy: Math.round(r.top + r.height / 2),
                    vw: window.innerWidth,
                    vh: window.innerHeight,
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                };
            }""",
            handle,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "_physical_click_element[{}]: getBoundingClientRect упал: {}",
            description, e,
        )
        if fallback_locator_click:
            try:
                await page.locator(selector).first.click()
            except Exception:  # noqa: BLE001
                pass
        return False
    cx = int(rect["cx"])
    cy = int(rect["cy"])
    vw = int(rect["vw"])
    vh = int(rect["vh"])
    bw = rect.get("w")
    bh = rect.get("h")
    if cx < 1 or cx > vw - 1 or cy < 1 or cy > vh - 1:
        logger.warning(
            "_physical_click_element[{}]: центр вне viewport "
            "(cx={}, cy={}, vw={}, vh={}, w={}, h={}) — фолбэк locator.click()",
            description, cx, cy, vw, vh, bw, bh,
        )
        if fallback_locator_click:
            try:
                await page.locator(selector).first.click()
            except Exception:  # noqa: BLE001
                pass
        return False
    try:
        await page.mouse.move(cx, cy)
        await asyncio.sleep(0.15)
        await page.mouse.click(cx, cy, delay=50)
        logger.info(
            "_physical_click_element[{}]: физ.клик ({},{}) bbox={}×{}",
            description, cx, cy, bw, bh,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "_physical_click_element[{}]: page.mouse.click упал: {} — "
            "фолбэк locator.click()", description, e,
        )
        if fallback_locator_click:
            try:
                await page.locator(selector).first.click()
            except Exception:  # noqa: BLE001
                pass
        return False


# Регэксп для валидации текста Generate-кнопки. Строгий
# case-sensitive prefix-match: текст ОБЯЗАН начинаться с заглавной
# 'Г' (Cyrillic U+0413) / 'С' (U+0421) / 'G' — это исключает Relax-
# кнопку с подписью 'РежимДешевле, но может генерировать
# дольше обычного' (lowercase 'г' в 'генерировать'), которую
# Playwright `:has-text('Генерировать')` ловит как substring
# (и case-insensitive!), и клик уходит в Relax вместо Generate.
_GENERATE_BUTTON_TEXT_RX = re.compile(
    r"^\s*(Генерировать|Сгенерировать|Создать|Generate)(\s.*)?$"
)


async def _find_and_physical_click_generate(
    page: Page,
    *,
    description: str = "Generate",
    timeout_ms: int = 60_000,
) -> tuple[bool, str, list[dict]]:
    """Находит выходную CTA-кнопку Generate С СТРОГИМ фильтром по
    тексту (должен начинаться с заглавной 'Генерировать' / и т.д.)
    и кликает по ней ФИЗИЧЕСКОЙ мышью через CDP.

    Зачем такая валидация текста: Playwright
    `button:has-text('Генерировать')` делает substring +
    case-insensitive match. На outsee.io в video-форме есть блок
    'Relax РежимДешевле, но может генерировать дольше
    обычного' — этот блок является button-элементом и его текст
    содержит подстроку 'генерировать' (lowercase). Playwright
    selector его ловит первым, и физ.клик уходит в Relax вместо
    настоящей кнопки Generate (подтверждено в прод логах).

    Регексп `_GENERATE_BUTTON_TEXT_RX` (case-sensitive, anchored ^)
    проверяет что текст начинается с заглавной 'Г' (или 'С',
    'С', 'G'), возможные варианты: 'Генерировать', 'Генерировать
    видео', 'Генерировать (5 кредитов)' и т.д.

    Параллельно проверяет visible + не disabled +
    bbox-в-viewport. Поллит до `timeout_ms`мс; возвращает
    `(success, clicked_text, candidates_diag)`, где candidates_diag —
    все «Генерировать»-кнопки которые видели (для диагностики).
    """
    candidates_diag: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    chosen_handle = None
    chosen_text = ""
    while asyncio.get_event_loop().time() < deadline:
        try:
            buttons = page.locator(
                "button:has-text('Генерировать'),"
                "button:has-text('Сгенерировать'),"
                "button:has-text('Создать'),"
                "button:has-text('Generate')"
            )
            count = await buttons.count()
            candidates_diag = []
            for i in range(min(count, 30)):
                loc = buttons.nth(i)
                try:
                    visible = await loc.is_visible()
                except Exception:  # noqa: BLE001
                    visible = False
                if not visible:
                    continue
                try:
                    handle = await loc.element_handle()
                except Exception:  # noqa: BLE001
                    handle = None
                if handle is None:
                    continue
                try:
                    info = await page.evaluate(
                        """(el) => {
                            const text = (
                                el.innerText || el.textContent || ''
                            ).trim();
                            const disabled = el.disabled === true
                                || el.hasAttribute('disabled')
                                || el.getAttribute('aria-disabled') === 'true';
                            return { text, disabled };
                        }""",
                        handle,
                    )
                except Exception:  # noqa: BLE001
                    continue
                text = (info.get("text") or "").strip()
                disabled = bool(info.get("disabled"))
                candidates_diag.append(
                    {"i": i, "text": text[:80], "disabled": disabled}
                )
                if disabled:
                    continue
                if _GENERATE_BUTTON_TEXT_RX.match(text):
                    chosen_handle = handle
                    chosen_text = text
                    break
            if chosen_handle is not None:
                break
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_find_and_physical_click_generate[{}]: scan error: {}",
                description, e,
            )
        await asyncio.sleep(0.5)

    if chosen_handle is None:
        logger.warning(
            "_find_and_physical_click_generate[{}]: НЕ нашли правильную "
            "Generate-кнопку за {}мс. Кандидаты: {}",
            description, timeout_ms, candidates_diag,
        )
        return False, "", candidates_diag

    # Скроллим, читаем bbox, физический CDP-клик
    try:
        await chosen_handle.scroll_into_view_if_needed(timeout=5_000)
    except Exception:  # noqa: BLE001
        pass
    try:
        rect = await page.evaluate(
            """(el) => {
                const r = el.getBoundingClientRect();
                return {
                    cx: Math.round(r.left + r.width / 2),
                    cy: Math.round(r.top + r.height / 2),
                    vw: window.innerWidth,
                    vh: window.innerHeight,
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                };
            }""",
            chosen_handle,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "_find_and_physical_click_generate[{}]: bbox упал: {} — "
            "фолбэк handle.click()", description, e,
        )
        try:
            await chosen_handle.click()
        except Exception:  # noqa: BLE001
            pass
        return False, chosen_text, candidates_diag

    cx = int(rect["cx"])
    cy = int(rect["cy"])
    vw = int(rect["vw"])
    vh = int(rect["vh"])
    bw = rect.get("w")
    bh = rect.get("h")
    if cx < 1 or cx > vw - 1 or cy < 1 or cy > vh - 1:
        logger.warning(
            "_find_and_physical_click_generate[{}]: bbox вне viewport "
            "(cx={}, cy={}, vw={}, vh={}, w={}, h={}, text={!r}) — "
            "фолбэк handle.click()",
            description, cx, cy, vw, vh, bw, bh, chosen_text,
        )
        try:
            await chosen_handle.click()
        except Exception:  # noqa: BLE001
            pass
        return False, chosen_text, candidates_diag

    try:
        await page.mouse.move(cx, cy)
        await asyncio.sleep(0.15)
        await page.mouse.click(cx, cy, delay=50)
        logger.info(
            "_find_and_physical_click_generate[{}]: физ.клик ({},{}) "
            "bbox={}×{} text={!r}",
            description, cx, cy, bw, bh, chosen_text,
        )
        return True, chosen_text, candidates_diag
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "_find_and_physical_click_generate[{}]: page.mouse.click "
            "упал: {} — фолбэк handle.click()", description, e,
        )
        try:
            await chosen_handle.click()
        except Exception:  # noqa: BLE001
            pass
        return False, chosen_text, candidates_diag


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
            # 0) АНТИ-ДУБЛИКАТ: если на странице УЖЕ есть карточка с
            # нашим `prompt_id_prefix` (например, прошлая попытка retry-
            # обёртки кликнула Generate, упала по таймауту/валидации, а
            # outsee тем временем продолжил рендерить и оставил карточку
            # в галерее) — НЕ кликаем Generate повторно. Иначе outsee
            # запустит ещё одну генерацию того же промта, и в истории
            # окажется 2-3 одинаковые «не-фейл» картинки.
            #
            # Детект — по количеству вхождений `[ID: ...]` токена в
            # тексте страницы. На свежей странице (attempt 1) count=0;
            # на retry, если карточка от прошлой попытки осталась — >=1.
            #
            # Если найденная карточка уже отрендерила картинку, исключаем
            # её URL из baseline, чтобы `_wait_image_url_strict` мог её
            # принять через priority-0 (prompt_id-match), а не отверг
            # как «была в baseline».
            already_in_progress = False
            pre_rejected_text: str | None = None
            if prompt_id_prefix:
                try:
                    _counts = await self._count_id_tokens_in_page(
                        page, [prompt_id_prefix]
                    )
                except Exception:  # noqa: BLE001
                    _counts = {}
                if _counts.get(prompt_id_prefix, 0) >= 1:
                    already_in_progress = True
                    existing_img = await self._find_img_by_prompt_id(
                        page, prompt_id_prefix
                    )
                    if existing_img:
                        existing_norm = _strip_url_query(existing_img)
                        baseline_big_imgs.discard(existing_norm)
                        baseline_dom_srcs.discard(existing_norm)
                        if baseline_result_img == existing_norm:
                            baseline_result_img = None
                    logger.warning(
                        "outsee.generate_image: на странице УЖЕ есть "
                        "карточка с {} (видимо прошлая попытка retry'я "
                        "кликнула Generate, а outsee продолжил рендерить) "
                        "— НЕ кликаю Generate повторно, жду результат "
                        "прошлого клика (gen_id={}, image_loaded={})",
                        prompt_id_prefix, gen_id[:8], bool(existing_img),
                    )

            if not already_in_progress:
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
                _relax_ok = await _toggle_relax(
                    page, want_on=relax, where="generate_image", dumps=dumps,
                )
                # Дать React'у дорисовать UI после смены Relax-режима до
                # того, как мы пойдём искать Generate-кнопку. Без этой
                # паузы Generate в редких случаях стартует со старым
                # Relax-состоянием (форма ещё не успела обновить
                # disabled/aria-атрибуты).
                if relax and not _relax_ok:
                    logger.warning(
                        "outsee.generate_image: Relax={} НЕ подтверждён — "
                        "продолжаю Generate с тем, что есть (gen_id={})",
                        relax, gen_id[:8],
                    )
                await asyncio.sleep(0.8)

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
                        )
                        if not attached:
                            h, p = await _dump_page(
                                page, f"ref_input_notfound_{ref_idx}"
                            )
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
                # Find + физ.клик Generate в одном шаге: хелпер
                # строго фильтрует по тексту (должен начинаться с
                # заглавной 'Г/С/G'), исключает Relax-ловушку и кликает
                # ФИЗИЧЕСКОЙ мышью через CDP. Предыдущий подход
                # (`_first_visible` + `_physical_click_element`) попадал в
                # Relax-кнопку в video-форме (см. _find_and_physical_
                # click_generate для обоснования). Для изображений баг
                # не воспроизводился, но единая логика = меньше рисков.
                (
                    _phys_ok,
                    _phys_text,
                    _phys_cands,
                ) = await _find_and_physical_click_generate(
                    page,
                    description="image:Generate",
                    timeout_ms=60_000,
                )
                logger.info(
                    "outsee.generate_image: Generate кликнут "
                    "(физ={}, text={!r}, candidates={}), жду картинку "
                    "(gen_id={})",
                    _phys_ok, _phys_text, len(_phys_cands), gen_id[:8],
                )
                if not _phys_ok:
                    h, p = await _dump_page(
                        page, "generate_btn_click_failed"
                    )
                    for x in (h, p):
                        if x:
                            dumps.append(x)
                    raise OutseeImageError(
                        "outsee image: Generate не кликнут "
                        f"(candidates={_phys_cands})",
                        context={
                            "gen_id": gen_id,
                            "candidates": _phys_cands,
                        },
                        dumps=dumps,
                    )

            # 4) Получаем НАШУ картинку.
            #
            # При `prompt_id_prefix` — manual-walk: проходим по ленте
            # новых тайлов, кликаем каждую как живой пользователь,
            # читаем `[ID: ...]` из правой панели открывшейся модалки,
            # сравниваем с нашим токеном, и при совпадении кликаем
            # download-иконку в overlay'е тайлы. Замена сразу для
            # двух подсистем: ожидания НАШЕЙ картинки (был
            # `_wait_image_url_strict`) и собственно скачивания (был
            # `_download_via_card_click`). См. подробное обоснование в
            # docstring'е `_capture_image_via_manual_walk`.
            #
            # При отсутствии `prompt_id_prefix` (legacy / recon-mode) —
            # используем старый URL-путь: `_wait_image_url_strict` +
            # `_download_via_context`.
            try:
                if prompt_id_prefix:
                    img_url = await _capture_image_via_manual_walk(
                        page,
                        prompt_id_prefix=prompt_id_prefix,
                        out_path=out_path,
                        baseline_all_srcs=baseline_dom_srcs,
                        timeout_s=timeout,
                        gen_id=gen_id,
                        content_rejected_check=(
                            lambda: self._content_rejected_text(page)
                        ),
                        pre_rejected_text=pre_rejected_text,
                    )
                else:
                    img_url = await self._wait_image_url_strict(
                        page,
                        timeout=timeout,
                        baseline_result_img=baseline_result_img,
                        baseline_big_imgs=baseline_big_imgs,
                        baseline_all_srcs=baseline_dom_srcs,
                        net_events=net_events,
                        gen_id=gen_id,
                        pre_rejected_text=pre_rejected_text,
                        prompt_id_prefix=None,
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

        # 5) При manual-walk файл уже сохранён внутри
        # `_capture_image_via_manual_walk` (через `page.expect_download`
        # на overlay-иконке тайлы). При legacy-пути — докачиваем по
        # URL через `_download_via_context`.
        if not prompt_id_prefix:
            try:
                await _download_via_context(page, img_url, out_path)
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

        # 5.1) Валидация скачанного файла. Manual-walk сохраняет байты,
        # пришедшие из `page.expect_download()` — никаких placeholder'ов
        # (`topaz.webp`, `input_*.png`) тут уже не подсунуть, но базовая
        # проверка (>50 KB + magic-байты PNG/JPEG/WebP) остаётся — на
        # случай битого CDN-ответа.
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
        prompt_id_prefix: str | None = None,
    ) -> GenerationResult:
        """Жмёт «Повторить» на существующем результате генерации — без ChatGPT,
        без перезаполнения промта. Сайт использует тот же промт и настройки.

        Параметры:
          prompt_id_prefix — ID нашей текущей генерации, например
                             `[ID: P2-F1-1614874f]`. Outsee использует тот же
                             промт (с этим же ID-префиксом), и новая картинка
                             будет иметь тот же `[ID: ...]` в модалке. Если
                             передан — после клика «Повторить» используем
                             `_capture_image_via_manual_walk` для поиска и
                             скачивания НАШЕЙ новой тайлы. Иначе — legacy
                             URL-путь (только для обратной совместимости).
        """
        import time as _time
        import uuid as _uuid

        gen_id = gen_id or _uuid.uuid4().hex
        page = await self.session.open_page(settings.outsee_image_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        baseline_result_img = _strip_url_query(await self._result_img_src(page))
        baseline_big_imgs = {
            _strip_url_query(u) for u in await self._all_big_imgs(page)
        }
        baseline_dom_srcs = {
            _strip_url_query(u) for u in await self._all_img_srcs(page)
        }

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
            # Физ.мышь через CDP вместо Playwright `locator.click()` —
            # см. _physical_click_element для обоснования.
            _phys_ok_retry = await _physical_click_element(
                page, retry_sel,
                description="image:Повторить",
            )
            logger.info(
                "outsee.regenerate_image: «Повторить» кликнут (физ={})",
                _phys_ok_retry,
            )
            logger.info(
                "outsee.regenerate_image: «Повторить» кликнут, жду картинку "
                "(gen_id={}, prompt_id_prefix={})",
                gen_id[:8], prompt_id_prefix,
            )

            if prompt_id_prefix:
                # Manual-walk: проходим по новым тайлам, читаем ID из
                # модалок, при совпадении кликаем download-иконку.
                # Файл сохраняется внутри функции.
                img_url = await _capture_image_via_manual_walk(
                    page,
                    prompt_id_prefix=prompt_id_prefix,
                    out_path=out_path,
                    baseline_all_srcs=baseline_dom_srcs,
                    timeout_s=timeout,
                    gen_id=gen_id,
                    content_rejected_check=(
                        lambda: self._content_rejected_text(page)
                    ),
                    pre_rejected_text=pre_rejected_text,
                )
            else:
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

        # Legacy URL-путь — только когда prompt_id_prefix не передан.
        # При manual-walk файл уже сохранён.
        if not prompt_id_prefix:
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

    async def _find_img_by_prompt_id(
        self,
        page: Page,
        id_token: str,
        *,
        max_levels: int = 12,
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
        # Готовим набор токенов для матчинга — от самого строгого
        # к самому либеральному. Cильные первыми, чтобы не подхватить
        # 8-hex-чужого uuid'а если случайно их два совпало.
        tokens: list[str] = [id_token]
        # Полное содержимое скобок: `P1-HERO1-V1-xxxxxxxx`.
        m = re.search(r"\[ID:\s*([A-Za-z0-9_-]+)\s*\]", id_token)
        if m:
            inner = m.group(1)
            if inner not in tokens:
                tokens.append(inner)
        # 8-hex-tail. uuid.uuid4().hex[:8] всегда даёт ровно 8 hex-символов.
        m2 = re.search(r"-([0-9a-fA-F]{8})\]?$", id_token)
        if m2:
            tail = m2.group(1)
            if tail and tail not in tokens:
                tokens.append(tail)

        js = """
        ([tokens, maxLevels]) => {
            // Хелпер: содержит ли элемент токен в видимом тексте
            // ИЛИ в .value, если это textarea/input.
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
            for (const idToken of tokens) {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (!el || !el.children) continue;
                    if (el === document.body || el === document.documentElement) continue;
                    if (!hasToken(el, idToken)) continue;
                    // Спускаемся к самому мелкому уровню, где нашёлся idToken,
                    // чтобы не схватить весь main с галереей.
                    let smallest = el;
                    for (const child of el.children) {
                        if (hasToken(child, idToken)) {
                            smallest = null;
                            break;
                        }
                    }
                    // Также проверяем «спрятанные» textarea/input внутри
                    // (если у el есть descendant textarea с нашим токеном,
                    // спустимся к нему).
                    if (smallest) {
                        const deepInputs = el.querySelectorAll('textarea, input');
                        for (const di of deepInputs) {
                            if (di === el) continue;
                            const v = di.value || '';
                            if (v.includes(idToken)) {
                                smallest = null;
                                break;
                            }
                        }
                    }
                    if (!smallest) continue;
                    // Поднимаемся вверх до уровня, где есть <img>.
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
                "_find_img_by_prompt_id: ошибка JS-поиска: {}", e
            )
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

        while asyncio.get_event_loop().time() < deadline:
            now = asyncio.get_event_loop().time()
            elapsed = now - start

            # 0) ВЫСШИЙ приоритет — поиск картинки по `prompt_id_prefix`.
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
                        logger.info(
                            "_wait_image_url_strict: matched by prompt_id "
                            "{} за {:.0f} сек: {}",
                            prompt_id_prefix,
                            elapsed,
                            by_id[:140],
                        )
                        return by_id

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
                    if _url_is_fresh(current, net_events):
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
                        # С ID-верификацией — запоминаем как fallback,
                        # но click-verify всё равно сработает (или net_events).
                        fallback_candidate = chosen
                        fallback_source = "new_dom"
                        # Диагностика: сколько «новых» набралось в список —
                        # если больше 1, то это признак re-sign или gallery
                        # refresh — логируем, чтобы было понятно в логе.
                        if len(clean) > 1:
                            logger.info(
                                "_wait_image_url_strict: new_srcs={} (>1) — "
                                "беру первый по DOM (новейший в outsee), "
                                "проверю click/net_events: {}",
                                len(clean), chosen[:120],
                            )

            # 2.7) Если у нас есть fallback_candidate, ВЕРИФИЦИРУЕМ его.
            #
            # Иерархия доверия (от сильного к слабому):
            #  A. net_events: URL РЕАЛЬНО пришёл по сети ПОСЛЕ нашего
            #     клика Generate. Listener чист в момент клика. Outsee
            #     не подгружает чужие изображения в этот короткий
            #     промежуток. Это самое сильное доказательство «это
            #     наша картинка», и его достаточно — клик-верификация
            #     не нужна.
            #  B. click-verification: клик по img открывает правую
            #     панель «Промпт», в видимом тексте которой должен
            #     появиться наш [ID: ...]. Слабее: outsee может
            #     рендерить промт через <textarea>.value, который не
            #     попадает в body.innerText, и счётчик токенов не
            #     растёт после клика → ложное «чужая». Использовать
            #     только как fallback, когда net_events недоступны
            #     или пусты.
            if (
                prompt_id_prefix
                and fallback_candidate is not None
                and _strip_url_query(fallback_candidate)
                not in rejected_candidates
            ):
                # A. net_events trust path. Если URL пришёл по сети
                # после Generate-клика — это сильная гарантия, что
                # это наша картинка. Click-verification (которая
                # часто врёт из-за textarea.value vs innerText)
                # пропускаем.
                if net_events and _url_is_fresh(
                    fallback_candidate, net_events
                ):
                    logger.info(
                        "_wait_image_url_strict: trusted by net_events "
                        "(source={}, URL пришёл по сети после Generate) "
                        "за {:.0f} сек: {}",
                        fallback_source, elapsed,
                        fallback_candidate[:140],
                    )
                    return fallback_candidate
                # B. Fallback — click-verification, когда нет net_events
                # или URL не подтверждён сетью.
                ok = await self._verify_img_by_clicking(
                    page, fallback_candidate, prompt_id_prefix
                )
                if ok:
                    logger.info(
                        "_wait_image_url_strict: verified by click "
                        "(source={}) за {:.0f} сек: {}",
                        fallback_source, elapsed,
                        fallback_candidate[:140],
                    )
                    return fallback_candidate
                else:
                    logger.warning(
                        "_wait_image_url_strict: fallback {} НЕ "
                        "прошёл ID-верификацию (source={}) — это чужая "
                        "картинка из gallery, ждём дальше",
                        fallback_candidate[:100], fallback_source,
                    )
                    rejected_candidates.add(
                        _strip_url_query(fallback_candidate)
                    )
                    fallback_candidate = None
                    fallback_source = None
                    # Подождём ещё пока придёт НОВАЯ картинка.
                    await asyncio.sleep(2.0)
                    continue
            # 2.5) Детект плашки «Контент отклонён» (модерация).
            # Outsee показывает её прямо на странице — ждать дальше
            # бесполезно: токены уже возвращены, генерации не будет.
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

            await asyncio.sleep(1.0)

        # timeout — все кандидаты были отвергнуты ID-верификацией
        # (или вообще не появились). Падаем с диагностикой.
        big_now = set(await self._all_big_imgs(page))
        new_big = big_now - baseline_big_imgs
        all_now_srcs = set(await self._all_img_srcs(page))
        new_dom = all_now_srcs - baseline_all_srcs
        ctx: dict[str, Any] = {
            "gen_id": gen_id,
            "baseline_result_img": baseline_result_img,
            "last_result_img_src": last_seen_result,
            "new_big_imgs": ", ".join(list(new_big)[:3]) or "—",
            "new_dom_srcs_count": len(new_dom),
            "baseline_big_imgs": len(baseline_big_imgs),
            "rejected_count": len(rejected_candidates),
        }
        if prompt_id_prefix:
            ctx["prompt_id_prefix"] = prompt_id_prefix
            ctx["id_diag"] = await self._diag_id_in_page(page, prompt_id_prefix)
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
        import time as _time

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

        # 0.5) Явный UI-выбор модели через кнопку «Поменять».
        # URL `?model=<slug>` обычно прокидывает модель, но юзер хочет
        # чтобы бот гарантированно клик'нул выбор в UI. Если модель уже
        # стоит правильная — функция тихо вернёт True и ничего не сделает.
        try:
            await _select_video_model_via_button(page, model_slug)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee.generate_video: UI-выбор модели упал: {} — "
                "продолжаю с тем, что прокинулось через ?model=…", e,
            )

        # Сетевой listener — ловит ВСЕ ответы похожие на mp4-видео
        # outsee/yandex CDN. Зеркало `_on_response` из generate_image,
        # только под видео-фильтр. Используется как ПРИОРИТЕТНЫЙ источник
        # реального подписанного mp4-URL'а в `_wait_video_url` и
        # `_capture_video_via_thumb_click` (синтез mp4 из thumb даёт 403,
        # потому что X-Amz-Signature привязана к thumb-пути).
        click_ts = _time.monotonic()
        net_events: list[tuple[float, str]] = []  # (ts_offset_from_click, url)

        def _on_response(resp: Any) -> None:
            try:
                if not _is_candidate_video_response(resp):
                    return
                net_events.append((_time.monotonic() - click_ts, resp.url))
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_response)

        # 0) АНТИ-ДУБЛИКАТ (зеркало логики из generate_image): если на
        # странице УЖЕ есть карточка с нашим `prompt_id_prefix` (прошлая
        # попытка retry'я кликнула Generate, упала по таймауту, а outsee
        # продолжил рендерить видео) — НЕ кликаем Generate повторно.
        # Иначе в истории outsee окажется 2-3 одинаковых ролика одного
        # кадра, а аккаунт сожжёт лимиты на дубликатах.
        #
        # Видео генерится 5-15 минут — это ОЧЕНЬ дорогое действие
        # дублировать. Поэтому проверка тут даже важнее, чем для картинок.
        already_in_progress = False
        if prompt_id_prefix:
            try:
                _counts = await self._count_id_tokens_in_page(
                    page, [prompt_id_prefix]
                )
            except Exception:  # noqa: BLE001
                _counts = {}
            if _counts.get(prompt_id_prefix, 0) >= 1:
                already_in_progress = True
                logger.warning(
                    "outsee.generate_video: на странице УЖЕ есть карточка "
                    "с {} — НЕ кликаю Generate повторно, жду результат "
                    "прошлого клика (video рендерится 5-15 мин)",
                    prompt_id_prefix,
                )

        if not already_in_progress:
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
            logger.info(
                "outsee.generate_video: промт вставлен ({} симв)", len(prompt)
            )

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

            # 2.7) Relax (только для veo-3-1-fast по словам пользователя).
            # ВАЖНО: `_toggle_relax` теперь физически кликает мышью CDP'ом
            # и верифицирует что состояние «Безлимит» реально сменилось
            # ДО возврата управления. Это блокирует ситуацию «Generate
            # стартует со старым Relax-режимом, потому что предыдущий
            # синтетический клик не сработал, а флоу проскочил дальше».
            _relax_ok_v = await _toggle_relax(
                page, want_on=relax, where="generate_video",
            )
            if relax and not _relax_ok_v:
                logger.warning(
                    "outsee.generate_video: Relax={} НЕ подтверждён — "
                    "продолжаю Generate с тем, что есть (prompt_id={})",
                    relax, prompt_id_prefix,
                )
            # Пауза, чтобы React дорисовал форму после смены Relax-режима
            # (иначе Generate может стартовать на «полу-готовом» UI).
            await asyncio.sleep(0.8)

            # 3) загрузка стартового кадра (если передан).
            #
            # На новом UI outsee.io 2026 (классический дизайн) есть ДВЕ
            # карточки: «Первый кадр (опционально)» и «Последний кадр
            # (опционально)». У каждой свой `<input type="file">`. Раньше
            # код брал просто `input[type=file]` `.last` — это «Последний
            # кадр», что НЕВЕРНО для image-to-video (там нужен ПЕРВЫЙ).
            #
            # Алгоритм поиска (по убыванию точности):
            #   1) FIRST_FRAME_INPUT_SELECTORS — input под карточкой
            #      «Первый кадр»; даже если он скрыт, set_input_files
            #      работает (Playwright не требует видимости).
            #   2) Если общий input[type=file] на странице ровно один —
            #      используем его (single-input UI).
            #   3) Несколько input[type=file] в DOM: вычитаем тот, что
            #      под «Последний кадр», и берём первый из оставшихся.
            #   4) Падаем с понятной ошибкой.
            if start_frame is not None:
                attached = False
                # --- (1) Прицельный поиск «Первый кадр» ---
                for sel in FIRST_FRAME_INPUT_SELECTORS:
                    try:
                        loc = page.locator(sel).first
                        n = await loc.count()
                    except Exception:  # noqa: BLE001
                        n = 0
                    if n <= 0:
                        continue
                    try:
                        await loc.set_input_files(str(start_frame))
                        logger.info(
                            "outsee.generate_video: стартовый кадр {} "
                            "загружен в «Первый кадр» ({})",
                            start_frame.name, sel,
                        )
                        attached = True
                        break
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "outsee.generate_video: {}.set_input_files "
                            "упал: {} — пробую следующий селектор",
                            sel, e,
                        )

                # --- (2)/(3) Fallback по общему input[type=file] ---
                if not attached:
                    base = page.locator("input[type='file']")
                    try:
                        n_inputs = await base.count()
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "outsee.generate_video: locator count упал: {}",
                            e,
                        )
                        n_inputs = 0
                    if n_inputs <= 0:
                        raise RuntimeError(
                            "outsee video: не найден input[type=file] для "
                            "стартового кадра (count=0). Возможно, модель "
                            "не поддерживает image-to-video или сайт "
                            "изменил DOM."
                        )

                    # Один input → он и есть «Первый кадр» (single-input UI).
                    if n_inputs == 1:
                        try:
                            await base.first.set_input_files(str(start_frame))
                            logger.info(
                                "outsee.generate_video: стартовый кадр {} "
                                "загружен в единственный input (count=1)",
                                start_frame.name,
                            )
                            attached = True
                        except Exception as e:  # noqa: BLE001
                            raise RuntimeError(
                                f"outsee video: set_input_files в "
                                f"единственный input упал: {e}"
                            ) from e
                    else:
                        # Несколько input'ов. Узнаём индекс «Последнего
                        # кадра», чтобы НЕ грузить в него.
                        last_frame_idx = -1
                        for sel_lf in LAST_FRAME_INPUT_SELECTORS:
                            try:
                                lf_loc = page.locator(sel_lf).first
                                if (await lf_loc.count()) > 0:
                                    # Совпадение есть; вычислим элемент,
                                    # на который он указывает, и найдём
                                    # его позицию среди base.
                                    handle = await lf_loc.element_handle()
                                    if handle is None:
                                        break
                                    for i in range(n_inputs):
                                        bh = await base.nth(i).element_handle()
                                        if bh is None:
                                            continue
                                        same = await page.evaluate(
                                            "([a, b]) => a === b",
                                            [handle, bh],
                                        )
                                        if same:
                                            last_frame_idx = i
                                            break
                                    break
                            except Exception:  # noqa: BLE001
                                continue

                        # Берём первый input, который НЕ «Последний кадр».
                        target_idx = -1
                        for i in range(n_inputs):
                            if i != last_frame_idx:
                                target_idx = i
                                break
                        if target_idx < 0:
                            target_idx = 0
                        try:
                            await base.nth(target_idx).set_input_files(
                                str(start_frame)
                            )
                            logger.info(
                                "outsee.generate_video: стартовый кадр {} "
                                "загружен в input[{}] (count={}, "
                                "last_frame_idx={})",
                                start_frame.name, target_idx, n_inputs,
                                last_frame_idx,
                            )
                            attached = True
                        except Exception as e:  # noqa: BLE001
                            raise RuntimeError(
                                f"outsee video: set_input_files в "
                                f"input[{target_idx}] упал: {e} "
                                f"(count={n_inputs})"
                            ) from e
                await asyncio.sleep(1.0)

            # 3.5) СНИМОК baseline ВИДЕО/ТАМБОВ ДО клика Generate —
            # всё что уже в DOM (история outsee, чужие ролики) не считаем
            # «свежим». Это зеркало baseline-логики из generate_image.
            baseline_video_urls: set[str] = set()
            try:
                baseline_video_urls = set(
                    await self._all_video_like_urls(page)
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "outsee.generate_video: baseline snapshot упал: {}", e
                )
            logger.info(
                "outsee.generate_video: baseline urls={} будем игнорировать (это прошлые ролики из истории)",
                len(baseline_video_urls),
            )

            # 4) generate — зеркало generate_image:
            #    a) ищем кнопку
            #    b) ждём пока станет enabled (settings.json/aspect/relax/
            #       первый-кадр обычно прокидывают валидацию формы и
            #       до завершения disabled=true; без `_wait_button_enabled`
            #       один и тот же баг что в картинках — клик «впустую»
            #       по disabled-кнопке, никакого эффекта в DOM).
            gen_sel = await _first_visible(
                page, GENERATE_BUTTON_SELECTORS, timeout_ms=10_000
            )
            if not gen_sel:
                raise RuntimeError("outsee video: не найдена кнопка Generate")
            logger.info(
                "outsee.generate_video: кнопка Generate найдена ({})", gen_sel,
            )
            await self._wait_button_enabled(page, gen_sel, timeout_s=600)
            # gen_sel выше — это «первая видимая кнопка с текстом
            # 'Генерировать'» по старой логике. В video-форме она может
            # быть Relax-ловушкой (см. ниже), поэтому фактический клик
            # делаем НЕ по gen_sel, а через `_find_and_physical_click_
            # generate` со строгой валидацией текста.
            click_ts = _time.monotonic()
            net_events.clear()
            # Find + физ.клик Generate с СТРОГИМ фильтром по тексту.
            # Старый подход (`_first_visible` + `_physical_click_element`)
            # попадал в Relax-кнопку: её текст содержит 'может
            # генерировать', и селектор has-text/case-insensitive
            # ловил её первой — физ.клик уходил в Relax. См.
            # _find_and_physical_click_generate для полного разбора.
            (
                _phys_ok_v,
                _phys_text_v,
                _phys_cands_v,
            ) = await _find_and_physical_click_generate(
                page,
                description="video:Generate",
                timeout_ms=60_000,
            )
            logger.info(
                "outsee.generate_video: Generate кликнут (физ={}, "
                "text={!r}, candidates={}), жду видео "
                "(timeout={:.0f}с, prompt_id={})",
                _phys_ok_v, _phys_text_v, len(_phys_cands_v),
                timeout, prompt_id_prefix,
            )
            if not _phys_ok_v:
                await _dump_page(page, "generate_video_btn_click_failed")
                raise RuntimeError(
                    "outsee video: Generate не кликнут "
                    f"(candidates={_phys_cands_v})"
                )
            # Через ~3 сек проверим, сработал ли клик — должен появиться
            # spinner / placeholder, или хотя бы новый thumb-img. Если НЕТ —
            # дамп страницы в data/outsee_dumps/.
            try:
                await asyncio.sleep(3.0)
                after_urls = set(await self._all_video_like_urls(page))
                if after_urls == baseline_video_urls and not net_events:
                    logger.warning(
                        "outsee.generate_video: ЧЕРЕЗ 3с ПОСЛЕ КЛИКА GENERATE "
                        "в DOM СТОЛЬКО ЖЕ video-тамбов ({}) и net_events=0. "
                        "Похоже кнопка НЕ сработала — дамп страницы.",
                        len(baseline_video_urls),
                    )
                    await _dump_page(page, "generate_click_no_effect")
            except Exception:  # noqa: BLE001
                pass
        else:
            # already_in_progress: baseline пустой (результатом
            # считаем всё что на странице, привязанное к нашему [ID:]).
            baseline_video_urls = set()

        try:
            # 5) ждём результат — видео в карточке с нашим [ID: ...],
            # причём URL ДОЛЖЕН отличаться от baseline (т.е. свеже-
            # сгенеренный, а не чужой из истории).
            video_url = await self._wait_video_url(
                page,
                timeout=timeout,
                prompt_id_prefix=prompt_id_prefix,
                baseline_urls=baseline_video_urls,
                net_events=net_events,
            )

            out_path.parent.mkdir(parents=True, exist_ok=True)
            await _download_via_context(page, video_url, out_path)
            logger.info("outsee video saved → {}", out_path)
            return GenerationResult(file_path=out_path, raw_url=video_url)
        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:  # noqa: BLE001
                pass

    async def _all_video_like_urls(self, page: Page) -> list[str]:
        """Снимок baseline: все URL'ы, похожие на видео-карточки outsee.

        Собираем:
          - <video>.src, <source>.src;
          - <a href*=".mp4"> / <a download>;
          - <img src> где src содержит "video_..._thumb" (галерейная
            превьюшка готового видео).

        Все URL'ы НОРМАЛИЗУЕМ (срезаем `?X-Amz-Signature=...`), иначе
        re-sign на каждом ререндере делает «новые» URL'ы из тех же
        старых роликов — и baseline-сравнение бы ломалось.
        """
        try:
            urls = await page.evaluate(
                """() => {
                    const list = [];
                    document.querySelectorAll('video').forEach(v => {
                        if (v.src) list.push(v.src);
                        v.querySelectorAll('source').forEach(s => s.src && list.push(s.src));
                    });
                    document.querySelectorAll('a[download], a[href*=".mp4"]').forEach(a => {
                        if (a.href) list.push(a.href);
                    });
                    document.querySelectorAll('img').forEach(img => {
                        const s = img.src || '';
                        if (s.includes('video_') && s.includes('_thumb')) list.push(s);
                    });
                    return list;
                }"""
            )
        except Exception:  # noqa: BLE001
            urls = []
        return [_strip_url_query(u) for u in (urls or []) if u]

    async def _find_video_by_prompt_id(
        self,
        page: Page,
        id_token: str,
        *,
        max_levels: int = 12,
        baseline_urls: set[str] | None = None,
    ) -> tuple[str, bool] | None:
        """Зеркало `_find_img_by_prompt_id`, но для видео-карточек outsee.

        Возвращает кортеж `(url, is_real_mp4)` или `None`.
          is_real_mp4=True  — URL взят из `<a href*=.mp4>`, `<video>.src`
                              или `<source>.src`. Это РЕАЛЬНЫЙ подписанный
                              CDN-URL mp4-файла, его можно скачать
                              напрямую через `_download_via_context`.
          is_real_mp4=False — URL взят из `<img>` с `_thumb.jpg`. Это
                              JPEG-превьюшка, а НЕ mp4. Скачивать её
                              как mp4 нельзя — подпись `X-Amz-Signature`
                              привязана к пути thumb, попытка GET на
                              `.mp4`-вариант с той же подписью отдаст
                              HTTP 403 (см. _capture_video_via_thumb_click).

        КЛЮЧ: в галерее outsee финиш-видео отображается НЕ как `<video>`,
        а как `<img>` с `_thumb.jpg`-превьюшкой. Сам mp4 загружается ПОЗЖЕ
        (когда юзер кликает по карточке и открывается lightbox с
        `<video>` плеером).

        Алгоритм (зеркало _find_img_by_prompt_id):
          итерируемся по медиа-кандидатам (a[href*=mp4], <video>,
          img[src*=video_..._thumb]) → для каждого идём ВВЕРХ по DOM,
          останавливаясь когда bound-rect ancestor'а становится >60%
          viewport по ширине (защита от composer-textarea, в которой
          тоже лежит наш [ID: ...]). Если внутри ancestor'а виден
          токен — это НАША карточка, возвращаем (URL, is_real_mp4).

        Если передан `baseline_urls` (set нормализованных URL без
        query) — URL'ы из baseline НЕ возвращаем (это старые ролики).
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

        # baseline_urls приходит как set нормализованных URL (без
        # query). В JS делаем то же — стрипим query из кандидатов
        # перед сравнением. КЛЮЧЕВОЕ: кандидата с baseline-src НЕ
        # пихаем в список вообще, иначе при iterate-в-DOM-порядке
        # первым придёт thumb из composer-карточки (старый ролик),
        # его ancestor будет содержать наш [ID:] (textarea рядом)
        # → вернём baseline. Filter-out на старте решает.
        baseline_list = sorted(baseline_urls) if baseline_urls else []
        js = """
        ([tokens, maxLevels, baselineList]) => {
            const MAX_CARD_W = window.innerWidth * 0.6;
            const baselineSet = new Set(baselineList || []);
            const stripQ = (u) => (u || '').split('?')[0];
            const hasTokenInScope = (el, idToken) => {
                const txt = el.textContent || '';
                if (txt.includes(idToken)) return true;
                for (const ta of el.querySelectorAll('textarea, input')) {
                    if ((ta.value || '').includes(idToken)) return true;
                }
                return false;
            };
            const addCandidate = (arr, src, el, isReal) => {
                if (!src || src.startsWith('data:')) return;
                if (baselineSet.has(stripQ(src))) return;
                arr.push({ src, el, isReal: !!isReal });
            };
            const candidates = [];
            // РЕАЛЬНЫЕ mp4 URL (если outsee их вообще рендерит в карточке).
            document.querySelectorAll('a[href*=".mp4"], a[download]').forEach(a => {
                addCandidate(candidates, a.href, a, true);
            });
            document.querySelectorAll('video').forEach(v => {
                if (v.src && !v.src.startsWith('blob:')) {
                    addCandidate(candidates, v.src, v, true);
                }
                v.querySelectorAll('source').forEach(s => {
                    if (s.src && !s.src.startsWith('blob:')) {
                        addCandidate(candidates, s.src, s, true);
                    }
                });
            });
            // THUMB jpg — НЕ конвертируем в mp4 (подпись X-Amz-Signature
            // привязана к thumb-пути, прямой GET .mp4-варианта = 403).
            // Возвращаем как is_real=false, чтобы caller знал что нужен
            // клик по thumb + capture <video>.src из открывшегося lightbox.
            document.querySelectorAll('img').forEach(img => {
                const s = img.src || '';
                if (!s) return;
                if (!(s.includes('video_') && s.includes('_thumb'))) return;
                addCandidate(candidates, s, img, false);
            });
            for (const idToken of tokens) {
                for (const c of candidates) {
                    let cur = c.el.parentElement;
                    for (let i = 0; i < maxLevels && cur; i++) {
                        const r = cur.getBoundingClientRect();
                        if (r.width > MAX_CARD_W) break;
                        if (hasTokenInScope(cur, idToken)) {
                            return { url: c.src, isReal: c.isReal };
                        }
                        cur = cur.parentElement;
                    }
                }
            }
            return null;
        }
        """
        try:
            res = await page.evaluate(
                js, [tokens, max_levels, baseline_list]
            )
            if not res or not isinstance(res, dict):
                return None
            url = res.get("url")
            if not isinstance(url, str) or not url:
                return None
            is_real = bool(res.get("isReal"))
            # Доп. защита (на случай если в JS не зашёл baseline).
            if baseline_urls and _strip_url_query(url) in baseline_urls:
                return None
            return (url, is_real)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_find_video_by_prompt_id: ошибка JS-поиска: {}", e
            )
            return None

    async def _capture_video_via_thumb_click(
        self,
        page: Page,
        thumb_url: str,
        *,
        baseline_urls: set[str] | None = None,
        net_events: list[tuple[float, str]] | None = None,
        timeout_s: float = 45.0,
    ) -> str | None:
        """Кликает по thumb-`<img>` нашей карточки, чтобы outsee открыл
        lightbox/плеер и подгрузил настоящий mp4 (с правильной подписью
        `X-Amz-Signature`). Возвращает реальный mp4-URL или None.

        Почему это нужно: thumb-URL вида `.../video_XXX_thumb.jpg?...`
        имеет подпись для пути thumb. Прямой GET на `.mp4`-вариант с той
        же подписью отдаёт HTTP 403. Реальный mp4-URL прилетает только
        когда страница его реально подгружает (lightbox с `<video>`).

        Стратегия:
          0) Достаём из thumb-URL номер `video_XXX` — ключ матчинга.
          1) Сначала проверяем `net_events`: outsee мог сам подгрузить
             mp4 (превью, hover-preload). Если уже есть — возвращаем.
          2) Иначе кликаем по thumb-`<img>` (через JS, чтобы не зависеть
             от hit-test overlay).
          3) Ждём (до `timeout_s` сек) пока в `net_events` появится mp4
             с тем же `video_XXX`, ИЛИ пока в DOM появится `<video>` с
             подходящим src.
        """
        norm = _strip_url_query(thumb_url)
        seg = norm.rsplit("/", 1)[-1]  # video_1779114886169_thumb.jpg
        m = re.search(r"video_(\d+)", seg)
        if not m:
            logger.warning(
                "_capture_video_via_thumb_click: не удалось извлечь "
                "video_XXX из thumb: {}", seg,
            )
            return None
        video_id = m.group(1)

        # (1) Сначала — может, mp4 уже прилетел через сеть.
        def _scan_net_events() -> str | None:
            if not net_events:
                return None
            target = f"video_{video_id}.mp4"
            for _, url in net_events:
                if target in url and not url.startswith("blob:"):
                    norm_u = _strip_url_query(url)
                    if not baseline_urls or norm_u not in baseline_urls:
                        return url
            return None

        already = _scan_net_events()
        if already:
            logger.info(
                "_capture_video_via_thumb_click: mp4 уже в net_events "
                "(клик не нужен) → {}", already[:120],
            )
            return already

        # (2) Кликаем по thumb-img — ФИЗИЧЕСКОЙ мышью через CDP.
        # JS-вариант (img.click()) — fallback если bbox вне viewport.
        try:
            bbox = await page.evaluate(
                """(seg) => {
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        if ((img.src || '').includes(seg)) {
                            try {
                                img.scrollIntoView({block: 'center'});
                            } catch (e) {}
                            const r = img.getBoundingClientRect();
                            return {
                                cx: Math.round(r.left + r.width / 2),
                                cy: Math.round(r.top + r.height / 2),
                                vw: window.innerWidth,
                                vh: window.innerHeight,
                                w: Math.round(r.width),
                                h: Math.round(r.height),
                            };
                        }
                    }
                    return null;
                }""", seg,
            )
            if not bbox:
                logger.warning(
                    "_capture_video_via_thumb_click: thumb с {} не "
                    "найден в DOM для клика", seg,
                )
                return None
            cx = int(bbox["cx"])
            cy = int(bbox["cy"])
            vw = int(bbox["vw"])
            vh = int(bbox["vh"])
            bw = bbox.get("w")
            bh = bbox.get("h")
            await asyncio.sleep(0.3)  # дать scrollIntoView отрисоваться
            if cx < 1 or cx > vw - 1 or cy < 1 or cy > vh - 1:
                logger.warning(
                    "_capture_video_via_thumb_click: bbox thumb вне "
                    "viewport (cx={}, cy={}, vw={}, vh={}, w={}, h={}) — "
                    "фолбэк JS .click()", cx, cy, vw, vh, bw, bh,
                )
                await page.evaluate(
                    """(seg) => {
                        const imgs = document.querySelectorAll('img');
                        for (const img of imgs) {
                            if ((img.src || '').includes(seg)) {
                                try { img.click(); } catch (e) {}
                                let cur = img.parentElement;
                                for (let i = 0; i < 6 && cur; i++) {
                                    const tag = (cur.tagName || '').toLowerCase();
                                    const role = cur.getAttribute('role');
                                    if (tag === 'button' || tag === 'a' ||
                                        role === 'button') {
                                        try { cur.click(); } catch (e) {}
                                        break;
                                    }
                                    cur = cur.parentElement;
                                }
                                return true;
                            }
                        }
                        return false;
                    }""", seg,
                )
            else:
                await page.mouse.move(cx, cy)
                await asyncio.sleep(0.15)
                await page.mouse.click(cx, cy, delay=50)
                logger.info(
                    "_capture_video_via_thumb_click: физ.клик ({},{}) "
                    "bbox={}×{} по thumb {}", cx, cy, bw, bh, seg,
                )
            logger.info(
                "_capture_video_via_thumb_click: клик по thumb {} "
                "выполнен, жду mp4 (timeout={:.0f}с)", seg, timeout_s,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_capture_video_via_thumb_click: клик упал: {}", e,
            )
            return None

        # (3) Ждём mp4 в net_events или <video> в DOM.
        deadline = asyncio.get_event_loop().time() + timeout_s
        target = f"video_{video_id}"
        while asyncio.get_event_loop().time() < deadline:
            via_net = _scan_net_events()
            if via_net:
                logger.info(
                    "_capture_video_via_thumb_click: mp4 пойман через "
                    "net_events → {}", via_net[:120],
                )
                return via_net
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
                        return list;
                    }"""
                )
            except Exception:  # noqa: BLE001
                urls = []
            for u in urls or []:
                if not u or u.startswith("blob:") or u.startswith("data:"):
                    continue
                if target in u and ".mp4" in u:
                    norm_u = _strip_url_query(u)
                    if not baseline_urls or norm_u not in baseline_urls:
                        logger.info(
                            "_capture_video_via_thumb_click: mp4 пойман "
                            "из <video>.src → {}", u[:120],
                        )
                        return u
            await asyncio.sleep(0.5)

        logger.warning(
            "_capture_video_via_thumb_click: за {:.0f}с mp4 с video_{} "
            "так и не прилетел", timeout_s, video_id,
        )
        return None

    async def _wait_video_url(
        self,
        page: Page,
        *,
        timeout: float,
        prompt_id_prefix: str | None = None,
        baseline_urls: set[str] | None = None,
        net_events: list[tuple[float, str]] | None = None,
    ) -> str:
        """Ждёт появление видео-результата в DOM.

        Стратегия (зеркало `_wait_image_url_strict`):
          0) Если есть `prompt_id_prefix` — ищем кандидата в карточке с
             нашим [ID: ...] через `_find_video_by_prompt_id` →
             возвращает (url, is_real_mp4):
               - is_real_mp4=True  → реальный mp4-URL, отдаём caller'у
                 на прямое скачивание.
               - is_real_mp4=False → это thumb jpg, клик по thumb через
                 `_capture_video_via_thumb_click` → ждём настоящий mp4
                 из net_events / <video>.src.
          1) Иначе (legacy без prompt_id_prefix) — берём первый НОВЫЙ
             (не-baseline) mp4-URL из net_events или из DOM.

        Логи прогресса каждые 15 секунд: сколько кандидатов и сколько
        из них новых.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        log_every = 15.0
        start_time = asyncio.get_event_loop().time()
        next_log = start_time + log_every
        while asyncio.get_event_loop().time() < deadline:
            # Приоритет 0: видео в карточке с [ID: ...].
            if prompt_id_prefix:
                try:
                    found = await self._find_video_by_prompt_id(
                        page, prompt_id_prefix, baseline_urls=baseline_urls,
                    )
                except Exception:  # noqa: BLE001
                    found = None
                if found is not None:
                    url, is_real = found
                    if is_real:
                        elapsed = (
                            asyncio.get_event_loop().time() - start_time
                        )
                        logger.info(
                            "outsee.generate_video: РЕАЛЬНЫЙ mp4-URL "
                            "найден в карточке с {} за {:.0f} сек → {}",
                            prompt_id_prefix, elapsed, url[:120],
                        )
                        return url
                    # is_real=False → это thumb; кликаем чтобы поднять
                    # lightbox и поймать настоящий mp4.
                    mp4 = await self._capture_video_via_thumb_click(
                        page, url,
                        baseline_urls=baseline_urls,
                        net_events=net_events,
                    )
                    if mp4:
                        elapsed = (
                            asyncio.get_event_loop().time() - start_time
                        )
                        logger.info(
                            "outsee.generate_video: mp4 пойман через "
                            "thumb-click ({}) за {:.0f} сек → {}",
                            prompt_id_prefix, elapsed, mp4[:120],
                        )
                        return mp4
            # Приоритет 1 (legacy без prompt_id_prefix): первый НОВЫЙ
            # mp4-URL. Сначала из net_events (там реальные подписанные
            # mp4-URL'ы, прилетевшие после клика Generate), затем — из
            # DOM (<video>.src / <a href*=.mp4>), но НЕ thumb (тот в
            # 99% случаев отдаст 403).
            if not prompt_id_prefix:
                if net_events:
                    for _, u in net_events:
                        if u.startswith("blob:") or u.startswith("data:"):
                            continue
                        if ".mp4" not in u:
                            continue
                        norm_u = _strip_url_query(u)
                        if baseline_urls and norm_u in baseline_urls:
                            continue
                        return u
                cur_urls = await self._all_video_like_urls(page)
                for u in cur_urls:
                    if u in (baseline_urls or set()):
                        continue
                    if u.startswith("blob:") or "_thumb" in u:
                        continue
                    if ".mp4" in u:
                        return u
            else:
                cur_urls = await self._all_video_like_urls(page)
            new_urls = [
                u for u in cur_urls
                if u not in (baseline_urls or set())
            ]
            now = asyncio.get_event_loop().time()
            if now >= next_log:
                logger.info(
                    "outsee.generate_video: жду результат… "
                    "{:.0f}/{:.0f}сек, видео-похожих в DOM={} "
                    "(из них новых={}), net_events={}, prompt_id={}",
                    now - start_time, timeout,
                    len(cur_urls or []),
                    len(new_urls or []),
                    len(net_events or []),
                    prompt_id_prefix,
                )
                next_log = now + log_every
            await asyncio.sleep(1.5)
        raise PWTimeoutError("outsee video: результат не появился за отведённое время")


async def _close_outsee_modal(
    page: Page, *, timeout_s: float = 2.5
) -> None:
    """Закрывает outsee-лайтбокс с картинкой (полноэкранный модальный
    оверлей, где справа видна `[ID: ...]`-плашка).

    Сначала пробует Escape — обычно этого достаточно. Если модалка
    осталась открыта (виден top-right `<button>` с `svg.lucide-x`), —
    кликает по этой кнопке через CDP-мышь (trusted-event). Это тот же
    путь, что и у живого пользователя — без `pointer-events`-сюрпризов.

    Best-effort: не падает, если ничего не вышло — caller всё равно
    перейдёт к следующей итерации walk-цикла.
    """
    with contextlib.suppress(Exception):
        await page.keyboard.press("Escape")

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.25)
        try:
            x_btn = await page.evaluate(
                """() => {
                    for (const btn of document.querySelectorAll(
                        'button, [role="button"]'
                    )) {
                        const svg = btn.querySelector('svg');
                        if (!svg) continue;
                        const cls = (svg.getAttribute('class') || '').toLowerCase();
                        if (!cls.includes('lucide-x')) continue;
                        const r = btn.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) continue;
                        // X-кнопка модалки — обычно верхний-правый угол
                        // viewport'а. Отсекаем все остальные lucide-x
                        // (например, кнопки удаления тегов).
                        if (r.top > window.innerHeight * 0.3) continue;
                        if (r.right < window.innerWidth * 0.5) continue;
                        return {
                            cx: Math.round(r.left + r.width / 2),
                            cy: Math.round(r.top + r.height / 2),
                        };
                    }
                    return null;
                }"""
            )
        except Exception:  # noqa: BLE001
            return
        if not x_btn:
            return
        with contextlib.suppress(Exception):
            await page.mouse.click(
                int(x_btn["cx"]), int(x_btn["cy"]), delay=50
            )
        await asyncio.sleep(0.4)


async def _detect_outsee_modal_id(page: Page) -> str | None:
    """Найти открытый outsee-лайтбокс (фуллскрин-модалку с превью + правой
    панелью промта) и извлечь из его DOM-содержимого `[ID: ...]`-токен.

    Зачем отдельная функция, а не просто regex по `body.innerText`:

      1. Outsee рендерит правую панель промта через `<textarea readonly>`,
         и `body.innerText` **не** включает `textarea.value` (это спека
         `innerText`). Без чтения textarea-значений `[ID: ...]` теряется.
      2. На странице постоянно живут другие `[ID: ...]`-токены (в композере,
         в индикаторе «генерация в процессе»). Нам нужен ID именно из
         модалки, а не «какой-то на странице». Поэтому ищем overlay-
         элемент и берём текст ТОЛЬКО из него.

    Считаем overlay'ем:
      * любой `[role="dialog"]` / `[aria-modal="true"]`;
      * либо fixed/absolute-позиционированный блок, занимающий ≥ 50%
        viewport по обеим осям с видимостью != hidden / opacity ≥ 0.3.

    Из такого overlay'я собираем `innerText + textarea.value + input.value`
    и матчим `[ID: ...]`. Если совпадений несколько — берём последнее
    (модалка обычно отрендерена в конце DOM).
    Возвращает `[ID: ...]` или None.
    """
    try:
        return await page.evaluate(
            """() => {
                const seen = new Set();
                const candidates = [];
                const add = (el) => {
                    if (!el || seen.has(el)) return;
                    seen.add(el);
                    candidates.push(el);
                };
                for (const el of document.querySelectorAll(
                    '[role="dialog"], [aria-modal="true"]'
                )) add(el);
                for (const el of document.querySelectorAll(
                    'div, section, aside'
                )) {
                    const cs = window.getComputedStyle(el);
                    if (cs.position !== 'fixed' && cs.position !== 'absolute') {
                        continue;
                    }
                    if (cs.visibility === 'hidden' || cs.display === 'none') {
                        continue;
                    }
                    if (parseFloat(cs.opacity || '1') < 0.3) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < window.innerWidth * 0.5) continue;
                    if (r.height < window.innerHeight * 0.5) continue;
                    add(el);
                }
                const re = /\\[ID:\\s*([A-Za-z0-9_-]+)\\s*\\]/g;
                let last = null;
                for (const el of candidates) {
                    let txt = el.innerText || el.textContent || '';
                    for (const ta of el.querySelectorAll('textarea')) {
                        if (ta.value) txt += '\\n' + ta.value;
                    }
                    for (const inp of el.querySelectorAll('input')) {
                        if (inp.value) txt += '\\n' + inp.value;
                    }
                    const matches = txt.match(re);
                    if (matches && matches.length > 0) {
                        last = matches[matches.length - 1];
                    }
                }
                return last;
            }"""
        )
    except Exception:  # noqa: BLE001
        return None


async def _capture_image_via_manual_walk(
    page: Page,
    *,
    prompt_id_prefix: str,
    out_path: Path,
    baseline_all_srcs: set[str],
    timeout_s: float = 600.0,
    gen_id: str | None = None,
    content_rejected_check: (
        Callable[[], Awaitable[str | None]] | None
    ) = None,
    pre_rejected_text: str | None = None,
) -> str:
    """Имитирует ручной флоу пользователя для скачивания НАШЕЙ картинки
    из правой ленты результатов на outsee.io.

    Зачем нужно: предыдущие подходы по факту не работают.

      * `_download_via_context(url)` — outsee регулярно отдаёт по этому
        URL не финальный файл, а плейсхолдер (`topaz.webp` пока идёт
        upscale, `input_*.png` — ссылка на наш же референс, re-signed
        CDN-URL с истёкшей подписью). Сохраняем мусор как наш файл.
      * `_download_via_card_click(prompt_id_prefix)` — ищет `[ID: ...]`
        в DOM через `get_by_text`, но юзер указал, что ID-плашка
        отрендерена ТОЛЬКО при наведении курсором или клике на тайлу;
        в обычном состоянии либо её нет в видимом DOM, либо текст
        приходит из `<textarea>`-промта (его `get_by_text` тоже находит,
        и `xpath=ancestor::*[…lucide-download…]` тыкает в карточку
        композера, а не в нашу).
      * `Locator.screenshot()` — теряет разрешение (пере-рисовка через
        `Page.captureScreenshot`, всегда PNG, DPR × CSS-size).

    Подход (точная имитация ручного клика):
      1. Сканируем ленту тайлов: все `<img>` в DOM, у которых нормализованный
         (host+path, без `?X-Amz-Signature=...`) src **не** в baseline_all_srcs
         **и** не в локальном `seen`-наборе.
      2. Для каждой новой тайлы (в порядке появления в DOM):
         a. CDP-мышь → центр bbox тайлы → click. Это `Input.dispatchMouseEvent`,
            trusted-event с точки зрения браузера — hit-test проходит так
            же, как и у живой мыши.
         b. Ждём появления модалки (до 6 сек). Детектим через рост
            количества `[ID: ...]`-токенов в `document.body.innerText`:
            outsee рендерит ID в правой панели лайтбокса видимым текстом
            (в отличие от `<textarea>`-промта, который в innerText не
            попадает).
         c. Извлекаем `[ID: ...]` из модалки. Сравниваем с
            `prompt_id_prefix` на 3 уровнях: full → inner (`P*-F*-8hex`) →
            8-hex tail. То же сравнение, что в `_find_img_by_prompt_id`.
         d. **MATCH** → закрываем модалку (`Esc` → fallback клик по X),
            наводим мышь на тайлу, ждём отрисовки action-overlay'я
            (`opacity-transition`), кликаем `<button>` с `svg.lucide-download`
            внутри карточки тайлы (это та самая стрелка-вниз в правом-
            верхнем углу превью), оборачиваем в `page.expect_download()` —
            сохраняем РЕАЛЬНЫЙ финальный файл от outsee в `out_path`.
         e. **NO MATCH** → закрываем модалку, помечаем тайлу как `seen`,
            отводим мышь в (0,0), переходим к следующей тайле.
      3. Если за `timeout_s` ни одна новая тайла не показала наш ID —
         `OutseeImageError`.

    Параметры:
      prompt_id_prefix  — наш ID-токен, например `[ID: P2-F1-1614874f]`.
      out_path          — куда положить скачанный файл.
      baseline_all_srcs — нормализованные src картинок, ужe бывших на
                          странице до клика Generate (или Повторить).
                          Эти тайлы пропускаем — они не наши.
      timeout_s         — лимит ожидания.
      gen_id            — для трейсинга в исключениях/логах.

    Возвращает: `img.src` найденной (и скачанной) тайлы — для лога.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Готовим набор токенов сравнения. Логика та же, что в
    # `_find_img_by_prompt_id`: от строгого к либеральному.
    tokens: list[str] = [prompt_id_prefix]
    m = re.search(r"\[ID:\s*([A-Za-z0-9_-]+)\s*\]", prompt_id_prefix)
    if m:
        inner = m.group(1)
        if inner not in tokens:
            tokens.append(inner)
    m2 = re.search(r"-([0-9a-fA-F]{8})\]?$", prompt_id_prefix)
    if m2:
        tail = m2.group(1)
        if tail and tail not in tokens:
            tokens.append(tail)

    def _modal_id_matches(modal_id: str) -> bool:
        for tok in tokens:
            if tok and tok in modal_id:
                return True
        return False

    seen: set[str] = set()
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_log = 0.0
    iteration = 0

    while asyncio.get_event_loop().time() < deadline:
        iteration += 1
        elapsed = timeout_s - (deadline - asyncio.get_event_loop().time())

        # 0) Проверка модерации (плашка «Контент отклонён»). Если outsee
        # отклонил промт, новых тайлов вообще не будет — walk бы уходил в
        # таймаут на 600 сек. Опрашиваем `content_rejected_check` и при
        # появлении НОВОЙ плашки (отличающейся от `pre_rejected_text` —
        # остатка предыдущей генерации) — пробрасываем
        # OutseeContentRejectedError, как это делал старый
        # `_wait_image_url_strict`.
        if content_rejected_check is not None and elapsed >= 3.0:
            try:
                rejected = await content_rejected_check()
            except Exception:  # noqa: BLE001
                rejected = None
            if rejected and rejected != pre_rejected_text:
                raise OutseeContentRejectedError(
                    "outsee image: контент отклонён модерацией",
                    context={
                        "gen_id": gen_id,
                        "rejection": rejected[:200],
                    },
                )

        # 1) Сканируем новые тайлы.
        #
        # Что отсеиваем:
        #   * baseline_all_srcs + seen — уже были на странице / уже
        #     обработаны нами;
        #   * `data:`-URL, неполные `<img>`, маленькие (<200 nat-px);
        #   * `/temp-images/` и `input_<digits>` — это наш референс,
        #     не результат генерации. Если кликнуть его, outsee откроет
        #     модалку, но там будет НЕ наш ID (или вообще другая
        #     карточка) — будем зря отсеивать.
        #   * bbox самой `<img>` < 100×100 (превьюшки/иконки).
        #
        # bbox для клика берём от САМОЙ `<img>`, не от ancestor'а.
        # Раньше мы шли parents → button|a → 5 уровней вверх, и в
        # outsee это приводило к выбору гигантского контейнера ленты
        # с центром далеко за пределами viewport — клик улетал в
        # пустоту. Сейчас bbox = bbox img → cx/cy внутри картинки →
        # клик гарантированно попадает на тайлу.
        try:
            tiles = await page.evaluate(
                """([baselineList, seenList]) => {
                    const stripQ = (u) => {
                        if (!u) return '';
                        const i = u.indexOf('?');
                        return i >= 0 ? u.slice(0, i) : u;
                    };
                    const skip = new Set();
                    for (const s of baselineList) skip.add(s);
                    for (const s of seenList) skip.add(s);
                    const out = [];
                    for (const img of document.querySelectorAll('img')) {
                        if (!img.src || img.src.startsWith('data:')) continue;
                        // input_<digits> или /temp-images/ — это твой
                        // референс, не сгенерированная картинка.
                        if (img.src.includes('/temp-images/')) continue;
                        if (/input_\\d+/.test(img.src)) continue;
                        if (!img.complete) continue;
                        if (!img.naturalWidth || img.naturalWidth < 200) continue;
                        const stable = stripQ(img.src);
                        if (skip.has(stable)) continue;
                        const r = img.getBoundingClientRect();
                        if (r.width < 100 || r.height < 100) continue;
                        out.push({
                            src: img.src,
                            srcNorm: stable,
                            // bbox самой <img> — не ancestor-walk.
                            left: Math.round(r.left),
                            top: Math.round(r.top),
                            width: Math.round(r.width),
                            height: Math.round(r.height),
                        });
                    }
                    return out;
                }""",
                [list(baseline_all_srcs), list(seen)],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "_capture_image_via_manual_walk: скан тайлов упал: {}", e,
            )
            tiles = []

        if not tiles:
            if elapsed - last_log > 15:
                last_log = elapsed
                logger.info(
                    "_capture_image_via_manual_walk: ждём... {:.0f} сек, "
                    "новых тайлов нет (seen={}, baseline={})",
                    elapsed, len(seen), len(baseline_all_srcs),
                )
            await asyncio.sleep(1.5)
            continue

        for tile in tiles:
            src_norm = tile["srcNorm"]
            seen.add(src_norm)

            logger.info(
                "_capture_image_via_manual_walk: пробую тайлу {} "
                "(bbox {}×{} @ ({},{}), iter={})",
                tile["src"][:80],
                tile["width"], tile["height"],
                tile["left"], tile["top"], iteration,
            )

            # 2a) Скроллим тайлу в видимую область и заново снимаем bbox.
            # Без этого outsee может вернуть нам тайлу из ленты ниже фолда —
            # центр клика окажется за пределами viewport (CDP-клик «попадёт»
            # туда, где ничего нет, и модалка не откроется).
            try:
                rect = await page.evaluate(
                    """([targetSrc]) => {
                        for (const img of document.querySelectorAll('img')) {
                            if (img.src !== targetSrc) continue;
                            img.scrollIntoView({
                                block: 'center', inline: 'center',
                            });
                            const r = img.getBoundingClientRect();
                            return {
                                cx: Math.round(r.left + r.width / 2),
                                cy: Math.round(r.top + r.height / 2),
                                vw: window.innerWidth,
                                vh: window.innerHeight,
                            };
                        }
                        return null;
                    }""",
                    [tile["src"]],
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "_capture_image_via_manual_walk: scrollIntoView упал: {}",
                    e,
                )
                rect = None
            if not rect:
                logger.warning(
                    "_capture_image_via_manual_walk: <img src={}> исчез из DOM "
                    "перед кликом — пропускаю",
                    tile["src"][:80],
                )
                continue
            cx, cy, vw, vh = (
                int(rect["cx"]), int(rect["cy"]),
                int(rect["vw"]), int(rect["vh"]),
            )
            if cx < 5 or cx > vw - 5 or cy < 5 or cy > vh - 5:
                logger.warning(
                    "_capture_image_via_manual_walk: после scrollIntoView центр "
                    "тайлы всё ещё вне viewport (cx={}, cy={}, vw={}, vh={}) — "
                    "пропускаю",
                    cx, cy, vw, vh,
                )
                continue
            # Даём странице 250ms на завершение скролла (smooth-behaviour
            # outsee'я + любые animation-кадры).
            await asyncio.sleep(0.25)

            # 2b) CDP-клик по тайле — trusted-event, как реальной мышью.
            try:
                await page.mouse.move(cx, cy)
                await asyncio.sleep(0.15)
                await page.mouse.click(cx, cy, delay=50)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "_capture_image_via_manual_walk: click тайлы упал: {}",
                    e,
                )
                continue

            # 2c) Polling модалки: ждём пока появится overlay с нашим ID.
            # `_detect_outsee_modal_id` сканирует именно overlay-кандидат
            # (role=dialog / fixed-position >50%×50% viewport) и читает
            # его `innerText` + `textarea.value` + `input.value` — потому
            # что outsee выкладывает промт в `<textarea readonly>`, и его
            # `.value` НЕ попадает в `document.body.innerText`. Поэтому
            # старый счётчик `[ID:]`-токенов по innerText не рос после
            # клика → walk считал что модалка не открылась.
            modal_id: str | None = None
            for _ in range(24):  # 24 × 0.25с = 6 сек
                await asyncio.sleep(0.25)
                modal_id = await _detect_outsee_modal_id(page)
                if modal_id:
                    break

            if modal_id is None:
                logger.warning(
                    "_capture_image_via_manual_walk: модалка не появилась "
                    "после клика тайлы {} (cx={}, cy={}) — закрываю на "
                    "всякий случай, пропускаю",
                    tile["src"][:80], cx, cy,
                )
                await _close_outsee_modal(page)
                continue

            logger.info(
                "_capture_image_via_manual_walk: модалка показала ID={}",
                modal_id,
            )

            matched = _modal_id_matches(modal_id)

            # В любом случае закрываем модалку перед следующим действием
            # (download или поиск следующей тайлы).
            await _close_outsee_modal(page)

            if not matched:
                logger.info(
                    "_capture_image_via_manual_walk: ID НЕ наш "
                    "(modal={}, target={}) — следующая тайла",
                    modal_id, prompt_id_prefix,
                )
                with contextlib.suppress(Exception):
                    await page.mouse.move(0, 0)
                continue

            # 2d) MATCH. Скачиваем эту тайлу.
            logger.info(
                "_capture_image_via_manual_walk: ID совпал → скачиваю {}",
                tile["src"][:80],
            )

            # Hover тайлу — action-overlay появляется по `:hover`,
            # `opacity-transition` ~200ms.
            try:
                await page.mouse.move(tile["cx"], tile["cy"])
                await asyncio.sleep(0.5)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "_capture_image_via_manual_walk: hover тайлы упал: {}",
                    e,
                )

            # Находим координаты стрелки-вниз: поднимаемся вверх от <img>
            # до ancestor'а, в поддереве которого есть `<button>` с
            # `svg.lucide-download`. Это та же иконка, что в action-bar'е.
            try:
                dl_info = await page.evaluate(
                    """([targetSrc]) => {
                        let img = null;
                        for (const i of document.querySelectorAll('img')) {
                            if (i.src === targetSrc) { img = i; break; }
                        }
                        if (!img) return null;
                        let cur = img.parentElement;
                        for (let i = 0; i < 10; i++) {
                            if (!cur) break;
                            const buttons = cur.querySelectorAll('button');
                            for (const btn of buttons) {
                                const svg = btn.querySelector('svg');
                                if (!svg) continue;
                                const cls = (
                                    svg.getAttribute('class') || ''
                                ).toLowerCase();
                                if (!cls.includes('lucide-download')) continue;
                                const r = btn.getBoundingClientRect();
                                if (r.width <= 0 || r.height <= 0) continue;
                                return {
                                    cx: Math.round(r.left + r.width / 2),
                                    cy: Math.round(r.top + r.height / 2),
                                };
                            }
                            cur = cur.parentElement;
                        }
                        return null;
                    }""",
                    [tile["src"]],
                )
            except Exception as e:  # noqa: BLE001
                dl_info = None
                logger.warning(
                    "_capture_image_via_manual_walk: поиск download-иконки "
                    "упал: {}", e,
                )

            if dl_info is None:
                raise OutseeImageError(
                    "outsee image: ID совпал, но кнопка-стрелка «скачать» в "
                    "overlay'е тайлы не найдена (action-bar не появился "
                    "после hover?)",
                    context={
                        "gen_id": gen_id,
                        "img_url": tile["src"][:200],
                        "modal_id": modal_id,
                    },
                )

            # CDP-клик по download-иконке + захват файла.
            try:
                async with page.expect_download(
                    timeout=int(timeout_s * 1000)
                ) as dl_ctx:
                    await page.mouse.click(
                        int(dl_info["cx"]),
                        int(dl_info["cy"]),
                        delay=50,
                    )
                download = await dl_ctx.value
                await download.save_as(str(out_path))
            except PWTimeoutError as e:
                raise OutseeImageError(
                    "outsee image: клик по download-иконке не вызвал "
                    "page.expect_download за отведённое время",
                    context={
                        "gen_id": gen_id,
                        "img_url": tile["src"][:200],
                        "modal_id": modal_id,
                        "timeout_s": timeout_s,
                        "err": f"{type(e).__name__}: {e}",
                    },
                ) from e
            except Exception as e:  # noqa: BLE001
                raise OutseeImageError(
                    "outsee image: download через CDP-клик по overlay-"
                    "иконке упал",
                    context={
                        "gen_id": gen_id,
                        "img_url": tile["src"][:200],
                        "modal_id": modal_id,
                        "err": f"{type(e).__name__}: {e}",
                    },
                ) from e

            logger.info(
                "_capture_image_via_manual_walk: сохранил файл {} "
                "(modal_id={}, src={})",
                out_path, modal_id, tile["src"][:60],
            )
            return tile["src"]

        # После прохода по всем тайлам, если ни одна не подошла —
        # подождём, пока появится следующая.
        await asyncio.sleep(0.5)

    raise OutseeImageError(
        f"outsee image: за {int(timeout_s)} сек ни одна новая тайла не "
        f"показала наш {prompt_id_prefix} в модалке",
        context={
            "gen_id": gen_id,
            "prompt_id_prefix": prompt_id_prefix,
            "seen_count": len(seen),
            "baseline_count": len(baseline_all_srcs),
        },
    )


async def _download_via_card_click(
    page: Page,
    *,
    prompt_id_prefix: str,
    out_path: Path,
    timeout_s: float = 120.0,
) -> None:
    """Кликает «↓ Скачать» на карточке результата с нашим
    `[ID: P{...}-F{...}-{8hex}]` и сохраняет реальный финальный файл
    через `page.expect_download()`.

    Преимущество перед старым `_download_via_context(page, img_url, ...)`:
    мы НЕ извлекаем URL из `<img src>` — outsee часто кладёт туда
    плейсхолдер (например `topaz.webp` пока работает upscale, или
    `input_*.png` — ссылку на наш же референс). Реальный финальный
    PNG/JPEG отдаётся ТОЛЬКО при клике по кнопке «Download».

    Логика:
      1) ищем элемент с текстом `prompt_id_prefix` в DOM (НЕ требуем
         visible — outsee рендерит этот текст в overlay с
         `opacity-0 group-hover:opacity-100`, поэтому элемент есть в
         DOM, но visibility=0 пока не наведут мышь);
      2) поднимаемся к ближайшему ancestor'у, у которого в поддереве
         есть и `<img>`, и `<button>` — это карточка-обёртка с классом
         `group`, на которой висит `group-hover` для overlay-а;
      3) скроллим её в видимую часть, hover на `<img>` (overlay имеет
         `pointer-events-none` пока невидим, поэтому hover именно на
         картинку, а не на overlay);
      4) ищем кнопку Download по каскаду селекторов
         (svg.lucide-download → title="Скачать" → title="Download" →
         aria-label) — outsee периодически меняет разметку action-бара;
      5) `expect_download` + click → сохраняем файл по пути out_path.
    """
    deadline_ms = int(timeout_s * 1000)

    # 1) Якорь — элемент с нашим уникальным [ID: ...] токеном.
    #    state="attached" (а НЕ "visible"), потому что overlay скрыт
    #    через `opacity-0 group-hover:opacity-100` — текст есть в DOM,
    #    но визуально невидим пока не наведут мышь.
    id_el = page.get_by_text(prompt_id_prefix, exact=False).first
    try:
        await id_el.wait_for(state="attached", timeout=deadline_ms)
    except PWTimeoutError as e:
        raise OutseeImageError(
            "outsee image: не нашёл карточку с нашим ID за время ожидания "
            "(текст промта не появился в DOM)",
            context={
                "prompt_id_prefix": prompt_id_prefix,
                "timeout_s": timeout_s,
            },
        ) from e

    # 2) Карточка = ближайший ancestor, у которого В ПОДДЕРЕВЕ есть
    #    и <img>, и <button>. Это .group-обёртка с group-hover.
    #    Раньше искали по svg.lucide-download, но outsee перестроил
    #    разметку и иконку download может не быть в нужном виде —
    #    более устойчиво искать просто по комбинации img+button.
    card = id_el.locator(
        "xpath=ancestor::*[descendant::img and descendant::button][1]"
    )

    # Скроллим карточку в видимую часть — иначе hover ничего не даст.
    with contextlib.suppress(Exception):
        await card.scroll_into_view_if_needed(timeout=5_000)

    # 3) Hover на <img> карточки (overlay имеет pointer-events-none
    #    пока невидим, поэтому hover именно на картинку, не на overlay).
    #    После этого `group-hover:` активирует overlay и action-кнопки
    #    становятся кликабельными.
    card_img = card.locator("img").first
    try:
        await card_img.hover(timeout=5_000)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "_download_via_card_click: hover на img упал ({}), "
            "пробуем force-hover на саму карточку",
            type(e).__name__,
        )
        with contextlib.suppress(Exception):
            await card.hover(timeout=5_000, force=True)

    # 4) Кнопка «Скачать» + клик через JS.
    #    Главная фишка: outsee оборачивает кнопки в overlay-div с
    #    `opacity-0 group-hover:opacity-100 pointer-events-none
    #    group-hover:pointer-events-auto`. Это значит что:
    #      - Playwright считает кнопку attached, но не «actionable»,
    #        потому что хит-тест браузера блокируется pointer-events:none.
    #      - `force=True` ОБХОДИТ Playwright actionability checks, но
    #        НЕ обходит pointer-events: браузер всё равно не доставит
    #        событие mouse в координату, где CSS говорит "не лови".
    #
    #    Решение: вызвать `.click()` НА ЭЛЕМЕНТЕ через JavaScript. Это
    #    не использует hit-testing — мы прямо триггерим click handler
    #    у DOM-элемента. CSS pointer-events на это не влияет.
    #
    #    Чтобы найти именно download-кнопку, используем большой JS-блок:
    #    он сам идёт от ID-текста вверх к карточке, находит action-bar,
    #    берёт 2-ю кнопку (или ищет lucide-download), и кликает.
    #    Возвращает src картинки из карточки — пригодится для URL-фолбэка.
    img_src_from_card: str | None = None
    click_via_js_err: str | None = None
    try:
        img_src_from_card = await page.evaluate(
            r"""
            (idPrefix) => {
                // 1) Найти первый текстовый элемент содержащий idPrefix.
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null
                );
                let textNode = null;
                while (walker.nextNode()) {
                    if (walker.currentNode.nodeValue
                        && walker.currentNode.nodeValue.includes(idPrefix)) {
                        textNode = walker.currentNode;
                        break;
                    }
                }
                if (!textNode) {
                    throw new Error('text node with [ID:] not found');
                }
                // 2) Подняться до карточки — ближайший ancestor содержащий и <img>, и <button>.
                let card = textNode.parentElement;
                while (card) {
                    if (card.querySelector('img') && card.querySelector('button')) {
                        break;
                    }
                    card = card.parentElement;
                }
                if (!card) {
                    throw new Error('card ancestor not found');
                }
                // 3) Сохранить src картинки для URL-фолбэка.
                const img = card.querySelector('img');
                const imgSrc = img ? img.src : null;
                // 4) Найти download-кнопку. Несколько стратегий:
                //    a) <button> с svg.lucide-download внутри
                //    b) <button title="Скачать"|"Download">
                //    c) 2-я <button> в любом div.absolute-блоке с >= 4 кнопками
                let btn = card.querySelector('button:has(svg.lucide-download)')
                    || card.querySelector('button[title="Скачать"]')
                    || card.querySelector('button[title="Download"]')
                    || card.querySelector('button[aria-label*="скач" i]')
                    || card.querySelector('button[aria-label*="download" i]');
                if (!btn) {
                    // Найти все divs с position-absolute и >= 4 кнопками — это action-bar.
                    const divs = Array.from(card.querySelectorAll('div'));
                    for (const d of divs) {
                        const cls = d.className || '';
                        if (typeof cls !== 'string') continue;
                        if (!cls.includes('absolute')) continue;
                        const btns = d.querySelectorAll(':scope > button');
                        if (btns.length >= 4) {
                            btn = btns[1];  // 2-я по эмпирике
                            break;
                        }
                    }
                }
                if (!btn) {
                    throw new Error('download button not found inside card');
                }
                // 5) Тык!
                btn.click();
                return imgSrc;
            }
            """,
            prompt_id_prefix,
        )
        logger.info(
            "_download_via_card_click: JS-click отправлен, img_src={}",
            (img_src_from_card[:120] if img_src_from_card else None),
        )
    except Exception as e:  # noqa: BLE001
        click_via_js_err = f"{type(e).__name__}: {e}"
        logger.warning(
            "_download_via_card_click: JS-click упал ({}), всё равно "
            "пробую expect_download а потом URL-фолбэк",
            click_via_js_err,
        )

    # 5) Пробуем поймать download через page.expect_download.
    #    Если outsee действительно скачивает через стандартный механизм
    #    (a[download] или Content-Disposition) — Playwright нам его отдаст.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    download_caught = False
    try:
        # expect_download нужно открывать ПЕРЕД click. Но JS-click уже улетел.
        # Если страница использует event-based download — это будет асинхронно,
        # дадим 8 секунд на ловлю.
        async with page.expect_download(timeout=8_000) as dl_info:
            # повторно триггерим click чтобы expect_download был открыт ДО клика.
            with contextlib.suppress(Exception):
                await page.evaluate(
                    r"""
                    (idPrefix) => {
                        const walker = document.createTreeWalker(
                            document.body, NodeFilter.SHOW_TEXT, null
                        );
                        let textNode = null;
                        while (walker.nextNode()) {
                            if (walker.currentNode.nodeValue
                                && walker.currentNode.nodeValue.includes(idPrefix)) {
                                textNode = walker.currentNode;
                                break;
                            }
                        }
                        if (!textNode) return;
                        let card = textNode.parentElement;
                        while (card) {
                            if (card.querySelector('img') && card.querySelector('button')) break;
                            card = card.parentElement;
                        }
                        if (!card) return;
                        let btn = card.querySelector('button:has(svg.lucide-download)')
                            || card.querySelector('button[title="Скачать"]')
                            || card.querySelector('button[title="Download"]')
                            || card.querySelector('button[aria-label*="скач" i]')
                            || card.querySelector('button[aria-label*="download" i]');
                        if (!btn) {
                            const divs = Array.from(card.querySelectorAll('div'));
                            for (const d of divs) {
                                const cls = d.className || '';
                                if (typeof cls !== 'string') continue;
                                if (!cls.includes('absolute')) continue;
                                const btns = d.querySelectorAll(':scope > button');
                                if (btns.length >= 4) { btn = btns[1]; break; }
                            }
                        }
                        if (btn) btn.click();
                    }
                    """,
                    prompt_id_prefix,
                )
        download = await dl_info.value
        await download.save_as(str(out_path))
        download_caught = True
        logger.info(
            "_download_via_card_click: получили download через expect_download → {}",
            out_path,
        )
    except PWTimeoutError:
        logger.warning(
            "_download_via_card_click: expect_download не сработал за 8 сек "
            "(outsee использует blob/fetch?), фолбэк на URL-загрузку"
        )

    # 6) Фолбэк: outsee может скачивать через blob URL (createObjectURL),
    #    тогда page.expect_download не сработает. Берём `<img src>` карточки
    #    и тянем напрямую через page.context.request — он имеет cookies,
    #    т.е. CDN отдаст нам реальный файл.
    if not download_caught:
        if not img_src_from_card:
            # Один последний шанс — попытаемся ещё раз достать src через JS.
            with contextlib.suppress(Exception):
                img_src_from_card = await page.evaluate(
                    r"""
                    (idPrefix) => {
                        const walker = document.createTreeWalker(
                            document.body, NodeFilter.SHOW_TEXT, null
                        );
                        let textNode = null;
                        while (walker.nextNode()) {
                            if (walker.currentNode.nodeValue
                                && walker.currentNode.nodeValue.includes(idPrefix)) {
                                textNode = walker.currentNode;
                                break;
                            }
                        }
                        if (!textNode) return null;
                        let card = textNode.parentElement;
                        while (card) {
                            if (card.querySelector('img')) break;
                            card = card.parentElement;
                        }
                        if (!card) return null;
                        const img = card.querySelector('img');
                        return img ? img.src : null;
                    }
                    """,
                    prompt_id_prefix,
                )
        if not img_src_from_card:
            raise OutseeImageError(
                "outsee image: ни expect_download, ни <img src> в карточке "
                "не сработали — скачивание невозможно",
                context={
                    "prompt_id_prefix": prompt_id_prefix,
                    "timeout_s": timeout_s,
                    "js_click_err": click_via_js_err or "—",
                },
            )
        try:
            await _download_via_context(page, img_src_from_card, out_path)
        except Exception as e:  # noqa: BLE001
            raise OutseeImageError(
                "outsee image: фолбэк-загрузка через page.context.request "
                "упала",
                context={
                    "prompt_id_prefix": prompt_id_prefix,
                    "img_src": img_src_from_card[:200],
                    "err": f"{type(e).__name__}: {e}",
                },
            ) from e
        logger.info(
            "_download_via_card_click: фолбэк через URL → {} (src={})",
            out_path, (img_src_from_card[:100] if img_src_from_card else None),
        )

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
