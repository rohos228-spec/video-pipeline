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


class OutseeImageError(RuntimeError):
    """Ошибка с описательным контекстом — пайплайн использует это,
    чтобы запостить понятную ошибку в Telegram, а не системный traceback."""

    def __init__(self, reason: str, *, context: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.context = dict(context or {})
        super().__init__(self.format_text())

    def format_text(self) -> str:
        lines = [self.reason]
        for k, v in self.context.items():
            s = str(v)
            if len(s) > 200:
                s = s[:200] + "…"
            lines.append(f"  {k}: {s}")
        return "\n".join(lines)


# Минимум «настоящей» картинки из nano-banana — она всегда тяжелее 50 KB
# (обычно 300 KB – 2 MB). Логотипы/аватары/иконки outsee ≤ 10 KB.
_MIN_IMAGE_BYTES = 50_000

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
    ) -> GenerationResult:
        import time as _time
        import uuid as _uuid

        gen_id = gen_id or _uuid.uuid4().hex
        logger.info(
            "outsee.generate_image: открываю страницу gen_id={}", gen_id[:8]
        )
        page = await self.session.open_page(settings.outsee_image_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        # Next.js-страница outsee гидратится дольше 3 сек — даём ей доразложиться.
        await page.wait_for_load_state("networkidle", timeout=30_000)
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
                raise OutseeImageError(
                    "outsee image: не найден ввод промта",
                    context={"gen_id": gen_id},
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

            # 2) выбрать 9:16 (best-effort)
            if aspect_ratio == "9:16":
                ar_sel = await _first_visible(page, ASPECT_9_16_SELECTORS, timeout_ms=4_000)
                if ar_sel:
                    try:
                        await page.locator(ar_sel).first.click()
                        logger.info("outsee.generate_image: 9:16 выбран ({})", ar_sel)
                    except Exception:  # noqa: BLE001
                        logger.warning("не удалось кликнуть по селектору 9:16 ({})", ar_sel)

            # 3) кнопка generate
            gen_sel = await _first_visible(page, GENERATE_BUTTON_SELECTORS, timeout_ms=10_000)
            if not gen_sel:
                raise OutseeImageError(
                    "outsee image: не найдена кнопка Generate",
                    context={"gen_id": gen_id},
                )
            logger.info("outsee.generate_image: кнопка Generate найдена ({})", gen_sel)
            await self._wait_button_enabled(page, gen_sel, timeout_s=600)

            click_ts = _time.monotonic()
            net_events.clear()
            await page.locator(gen_sel).first.click()
            logger.info(
                "outsee.generate_image: Generate кликнут, жду картинку (gen_id={})",
                gen_id[:8],
            )

            # 4) строгое ожидание свежей картинки
            img_url = await self._wait_image_url_strict(
                page,
                timeout=timeout,
                baseline_result_img=baseline_result_img,
                baseline_big_imgs=baseline_big_imgs,
                baseline_all_srcs=baseline_dom_srcs,
                gen_id=gen_id,
            )
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
            ) from e
        logger.info("outsee image saved → {} (gen_id={})", out_path, gen_id[:8])
        return GenerationResult(file_path=out_path, raw_url=img_url, gen_id=gen_id)

    async def regenerate_image(
        self,
        out_path: Path,
        *,
        timeout: float = 600,
        gen_id: str | None = None,
    ) -> GenerationResult:
        """Жмёт «Повторить» на существующем результате генерации — без ChatGPT,
        без перезаполнения промта. Сайт использует тот же промт и настройки."""
        import uuid as _uuid

        gen_id = gen_id or _uuid.uuid4().hex
        page = await self.session.open_page(settings.outsee_image_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=30_000)

        baseline_result_img = await self._result_img_src(page)
        baseline_big_imgs = set(await self._all_big_imgs(page))
        baseline_dom_srcs = set(await self._all_img_srcs(page))

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
                gen_id=gen_id,
            )
        finally:
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
        gen_id: str,
    ) -> str:
        """Жёсткое ожидание свежей картинки. Берётся ТОЛЬКО:

        1) `<img>` из блока «Результат генерации», у которого src отличается
           от baseline и который полностью загружен (img.complete &&
           naturalWidth >= 200);
        2) либо самая последняя `<img>`, появившаяся в DOM ПОСЛЕ нажатия
           Generate, прошедшая ту же проверку.

        Никаких «сетевых fallback-ов» и «возьму самую большую» — это и был
        источник косяков (приходила постер видосов / старая картинка из
        кэша). Если за timeout условие не сработало — кидаем
        OutseeImageError с подробным контекстом, что было/чего не хватило.
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
            ):
                # Дополнительно проверим, что эта картинка действительно
                # новая (её не было в baseline-srcs) и полностью загружена.
                if current not in baseline_all_srcs:
                    if await self._img_is_loaded(page, current):
                        logger.info(
                            "_wait_image_url_strict: «Результат генерации» "
                            "за {:.0f} сек: {}",
                            elapsed,
                            current[:140],
                        )
                        return current

            # 2) фоллбэк — новая ПОЛНОСТЬЮ ЗАГРУЖЕННАЯ <img> в DOM,
            # которой не было в baseline. Берём ПОСЛЕДНЮЮ в порядке DOM —
            # карточки результатов обычно добавляются в конец списка.
            new_srcs = await self._completed_new_imgs(page, baseline_all_srcs)
            if new_srcs:
                # фильтруем UI-ассеты по URL
                clean = [
                    u
                    for u in new_srcs
                    if not any(m in u.lower() for m in _UI_ASSET_MARKERS)
                ]
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

    # ----- VIDEO (veo-3-fast Relax) -----

    async def generate_video(
        self,
        prompt: str,
        out_path: Path,
        *,
        start_frame: Path | None = None,
        aspect_ratio: str = "9:16",
        timeout: float = 900,
    ) -> GenerationResult:
        page = await self.session.open_page(settings.outsee_video_url, reuse=True)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=30_000)

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

        # 2) аспект
        if aspect_ratio == "9:16":
            ar_sel = await _first_visible(page, ASPECT_9_16_SELECTORS, timeout_ms=4_000)
            if ar_sel:
                try:
                    await page.locator(ar_sel).first.click()
                except Exception:  # noqa: BLE001
                    pass

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
            await page.wait_for_load_state("networkidle", timeout=30_000)
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
