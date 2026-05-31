"""11Labs (elevenlabs.io) — генерация озвучки через web-интерфейс TTS.

Порядок:
  1) модель Eleven v3 (первая в «Select a model»),
  2) Settings → Voice → «Select a voice» → поиск по id → клик карточки,
  3) пауза 5–10 с,
  4) текст voiceover → Generate speech → mp3.

Разведка: `python -m app.bots.elevenlabs recon "тест"`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from loguru import logger
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PWTimeoutError

from app.bots.browser import BrowserSession, browser_session
from app.services.elevenlabs_voices import (
    DEFAULT_ELEVENLABS_VOICE_ID,
    ELEVENLABS_VOICES,
)
from app.settings import settings

INPUT_SELECTORS = [
    "textarea[data-testid='tts-textarea']",
    "textarea#tts-textarea",
    "textarea[placeholder*='text' i]",
    "textarea[placeholder*='Type' i]",
    "textarea[placeholder*='введите' i]",
    "textarea[placeholder*='Enter' i]",
    "textarea[name='text']",
    '[contenteditable="true"][role="textbox"]',
    "div[contenteditable='true']",
    "textarea",
]
GENERATE_SELECTORS = [
    "button:has-text('Regenerate speech')",
    "button:has-text('Generate speech')",
    "button:has-text('Generate')",
    "button:has-text('Сгенерировать')",
    "button:has-text('Create')",
    "button[type='submit']",
    "button[data-testid='generate']",
]
DOWNLOAD_SELECTORS = [
    "a[download][href*='.mp3']",
    "button:has-text('Download')",
    "button:has-text('Скачать')",
    "button[aria-label='Download']",
    "a[aria-label='Download']",
    "a[href*='.mp3']",
]

TTS_PAGE_PATHS = (
    "/app/speech-synthesis/text-to-speech",
    "/app/speech-synthesis",
    "/app/text-to-speech",
)

V3_MODEL_LABEL = "Eleven v3"
VOICE_SETUP_WAIT_SEC = 7.0


async def _ensure_settings_tab(page: Page) -> None:
    try:
        tab = page.get_by_role("tab", name="Settings")
        if await tab.count() and await tab.is_visible():
            await tab.click()
            await asyncio.sleep(0.35)
    except Exception:  # noqa: BLE001
        pass


async def _is_model_v3_active(page: Page) -> bool:
    return bool(
        await page.evaluate(
            """() => {
                const asides = [...document.querySelectorAll('aside')];
                const scope = asides.length ? asides[asides.length - 1] : document.body;
                for (const b of scope.querySelectorAll('button, [role="button"]')) {
                    const t = (b.innerText || '').trim();
                    if (/^Eleven v3\\b/i.test(t) || (t.includes('Eleven v3') && !/Multilingual|Flash|v2/i.test(t)))
                        return true;
                }
                return false;
            }"""
        )
    )


async def _first_visible(page: Page, selectors: list[str], *, timeout_ms: int = 20_000) -> str | None:
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                try:
                    if await loc.is_visible():
                        return sel
                except Exception:  # noqa: BLE001
                    continue
            except Exception:  # noqa: BLE001
                continue
        await asyncio.sleep(0.3)
    return None


async def _panel_open(page: Page, title: str) -> bool:
    try:
        loc = page.get_by_text(title, exact=False).first
        return await loc.is_visible()
    except Exception:  # noqa: BLE001
        return False


async def _dismiss_overlay_panels(page: Page) -> None:
    """Закрыть «Select a model» / «Select a voice», вернуться к Settings."""
    for _ in range(3):
        if not await _panel_open(page, "Select a model") and not await _panel_open(page, "Select a voice"):
            return
        try:
            back = page.locator(
                "button[aria-label*='back' i], button[aria-label*='Back' i], "
                "button:has-text('Back'), [data-testid*='back']"
            ).first
            if await back.count() and await back.is_visible():
                await back.click()
                await asyncio.sleep(0.5)
                continue
        except Exception:  # noqa: BLE001
            pass
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.4)


async def _ensure_tts_page(page: Page) -> None:
    base = settings.elevenlabs_web_url.rstrip("/")
    root = base.split("/app/")[0] if "/app/" in base else "https://elevenlabs.io"

    for path in TTS_PAGE_PATHS:
        url = f"{root}{path}"
        try:
            if path.split("/")[-1] in (page.url or ""):
                break
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(1.5)
            sel = await _first_visible(page, INPUT_SELECTORS, timeout_ms=8_000)
            if sel:
                logger.info("11Labs: страница TTS ok ({})", url)
                return
        except Exception as e:  # noqa: BLE001
            logger.warning("11Labs: TTS url {} — {}", url, e)

    await page.goto(settings.elevenlabs_web_url, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(2.0)


async def _open_model_picker(page: Page) -> None:
    await _dismiss_overlay_panels(page)
    if await _panel_open(page, "Select a model"):
        return

    await _ensure_settings_tab(page)

    for loc in (
        page.get_by_text("Model", exact=True).locator(
            "xpath=following::button[1]"
        ),
        page.locator("button").filter(has_text="Multilingual"),
        page.locator("button").filter(has_text="Flash v2"),
        page.locator("button").filter(has_text="Eleven v3"),
    ):
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(force=True, timeout=8_000)
                await asyncio.sleep(0.9)
                if await _panel_open(page, "Select a model"):
                    return
        except Exception:  # noqa: BLE001
            continue

    await page.evaluate(
        """() => {
            const lbl = [...document.querySelectorAll('*')].find(
                el => (el.innerText || '').trim() === 'Model' && el.children.length < 6
            );
            if (!lbl) return false;
            let p = lbl.parentElement;
            for (let i = 0; i < 10 && p; i++) {
                const btn = p.querySelector('button, [role="button"]');
                if (btn) { btn.click(); return true; }
                p = p.parentElement;
            }
            return false;
        }"""
    )
    await asyncio.sleep(0.9)


async def _click_v3_card_in_model_panel(page: Page) -> bool:
    """Клик по карточке Eleven v3 в списке «Select a model» (Playwright + React events)."""
    if not await _panel_open(page, "Select a model"):
        return False

    for loc in (
        page.get_by_text("Eleven v3", exact=True),
        page.locator("div, article, button, li").filter(
            has_text="Eleven v3"
        ).filter(has_text="expressive").first,
        page.locator("div, article, button, li").filter(
            has_text="Eleven v3"
        ).filter(has_not_text="Multilingual").filter(has_not_text="Flash").first,
    ):
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(force=True, timeout=8_000)
                await asyncio.sleep(0.8)
                return True
        except Exception:  # noqa: BLE001
            continue

    return bool(
        await page.evaluate(
            """() => {
                const fire = (el) => {
                    if (!el) return false;
                    el.scrollIntoView({ block: 'center' });
                    const o = { bubbles: true, cancelable: true, view: window };
                    for (const t of ['pointerdown','mousedown','mouseup','click'])
                        el.dispatchEvent(new MouseEvent(t, o));
                    return true;
                };
                const hdr = [...document.querySelectorAll('*')].find(
                    el => (el.innerText || '').trim() === 'Select a model'
                );
                let root = hdr?.closest('aside') || hdr?.parentElement;
                for (let d = 0; d < 12 && root; d++) {
                    const cards = [...root.querySelectorAll('div, article, button, li')].filter(el => {
                        const t = (el.innerText || '').trim();
                        if (!t.startsWith('Eleven v3')) return false;
                        if (/Multilingual|Flash|v2/i.test(t)) return false;
                        return t.length < 220;
                    });
                    cards.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                    if (cards.length && fire(cards[0])) return true;
                    root = root.parentElement;
                }
                return false;
            }"""
        )
    )


async def _select_v3_model(page: Page) -> None:
    """Модель Eleven v3 — с проверкой, что в Settings не остался Multilingual v2."""
    if await _is_model_v3_active(page):
        logger.info("11Labs: модель {} уже выбрана", V3_MODEL_LABEL)
        return

    for attempt in range(5):
        await _open_model_picker(page)
        if not await _panel_open(page, "Select a model"):
            logger.warning("11Labs: панель Select a model не открылась (попытка {})", attempt + 1)
            await asyncio.sleep(0.5)
            continue

        clicked = await _click_v3_card_in_model_panel(page)
        await asyncio.sleep(0.6)
        await _dismiss_overlay_panels(page)
        await asyncio.sleep(0.4)

        if await _is_model_v3_active(page):
            logger.info("11Labs: модель {} выбрана (попытка {})", V3_MODEL_LABEL, attempt + 1)
            return

        logger.warning(
            "11Labs: v3 не подтверждена после клика (clicked={}, попытка {})",
            clicked,
            attempt + 1,
        )

    raise RuntimeError(f"11Labs: не удалось выбрать модель {V3_MODEL_LABEL}")


async def _open_voice_panel(page: Page) -> None:
    await _dismiss_overlay_panels(page)
    if await _panel_open(page, "Select a voice"):
        return

    await _ensure_settings_tab(page)

    for loc in (
        page.get_by_text("Voice", exact=True).locator(
            "xpath=ancestor::*[1]/following-sibling::*//button[1]"
        ),
        page.get_by_text("Voice", exact=True).locator(
            "xpath=ancestor::*[2]//button[contains(., ' - ')]"
        ),
        page.locator("button").filter(has_text=" - ").filter(
            has_not_text="Generate"
        ).filter(has_not_text="Download"),
    ):
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(force=True, timeout=8_000)
                await asyncio.sleep(1.0)
                if await _panel_open(page, "Select a voice"):
                    return
        except Exception:  # noqa: BLE001
            continue

    await page.evaluate(
        """() => {
            const fire = (el) => {
                if (!el) return false;
                el.scrollIntoView({ block: 'center' });
                const o = { bubbles: true, cancelable: true, view: window };
                for (const t of ['pointerdown','mousedown','mouseup','click'])
                    el.dispatchEvent(new MouseEvent(t, o));
                return true;
            };
            const lbl = [...document.querySelectorAll('*')].find(
                el => (el.innerText || '').trim() === 'Voice' && el.children.length < 6
            );
            if (!lbl) return false;
            let p = lbl.parentElement;
            for (let i = 0; i < 12 && p; i++) {
                const btn = [...p.querySelectorAll('button, [role="button"]')].find(b => {
                    const t = (b.innerText || '').trim();
                    return t.length > 12 && t.includes(' - ') && !/Generate|Download/i.test(t);
                });
                if (btn && fire(btn)) return true;
                p = p.parentElement;
            }
            return false;
        }"""
    )
    await asyncio.sleep(1.0)
    if not await _panel_open(page, "Select a voice"):
        raise RuntimeError("11Labs: не открылась панель Select a voice (кликните Voice в Settings)")


async def _voice_search_input(page: Page) -> Locator:
    for attempt in range(8):
        if await _panel_open(page, "Select a voice"):
            break
        await _open_voice_panel(page)
        await asyncio.sleep(0.5)

    marked = await page.evaluate(
        """() => {
            document.querySelectorAll('[data-vp-voice-search]').forEach(
                el => el.removeAttribute('data-vp-voice-search')
            );
            const title = [...document.querySelectorAll('*')].find(el => {
                const t = (el.innerText || '').trim();
                return /^Select a voice$/i.test(t);
            });
            let root = title ? (title.closest('aside') || title.parentElement) : null;
            for (let i = 0; i < 14 && root; i++) {
                const inputs = [...root.querySelectorAll('input')].filter(inp => {
                    const type = (inp.getAttribute('type') || 'text').toLowerCase();
                    return !['hidden', 'checkbox', 'radio', 'file'].includes(type);
                });
                if (inputs.length) {
                    inputs[inputs.length - 1].setAttribute('data-vp-voice-search', '1');
                    return true;
                }
                root = root.parentElement;
            }
            const any = document.querySelector(
                'input[placeholder*="earch" i], input[placeholder*="voice" i], input[type="search"]'
            );
            if (any) {
                any.setAttribute('data-vp-voice-search', '1');
                return true;
            }
            return false;
        }"""
    )
    if marked:
        return page.locator("[data-vp-voice-search='1']").first

    for loc in (
        page.get_by_role("searchbox").first,
        page.locator("input[placeholder*='Search' i]").last,
        page.locator("input[placeholder*='earch' i]").last,
        page.locator("input[type='search']").last,
    ):
        try:
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:  # noqa: BLE001
            continue

    raise RuntimeError("11Labs: не найдено поле поиска голоса (откройте Voice в Settings)")


def _voice_name_hint(voice_id: str) -> str:
    for v in ELEVENLABS_VOICES:
        if v["id"] == voice_id:
            return v["name"]
    return ""


async def _voice_selection_done(page: Page, voice_id: str) -> bool:
    """Голос выбран: панель закрылась или на карточке есть selected/check."""
    await asyncio.sleep(0.4)
    if not await _panel_open(page, "Select a voice"):
        return True
    name_hint = _voice_name_hint(voice_id)
    return bool(
        await page.evaluate(
            """(vid, nameHint) => {
                const skip = /Select a voice|Explore|My Voices|Language:|Filters|Accent|Category|Search/i;
                const rows = [...document.querySelectorAll('div, article, li, button')];
                for (const row of rows) {
                    const t = (row.innerText || '').trim();
                    if (t.length < 12 || t.length > 350 || skip.test(t)) continue;
                    const match = t.includes(vid)
                        || (nameHint && t.toLowerCase().includes(nameHint.toLowerCase()) && t.includes(' - '));
                    if (!match) continue;
                    if (row.getAttribute('aria-selected') === 'true') return true;
                    if (row.querySelector('[aria-selected="true"], [data-selected="true"]')) return true;
                    if (row.querySelector('[class*="selected" i], [class*="checked" i]')) return true;
                }
                return false;
            }""",
            voice_id,
            name_hint,
        )
    )


async def _click_voice_result_card(page: Page, voice_id: str, search: Locator) -> bool:
    """Выбор первого результата под поиском: клавиатура, клик по координатам, клик по строке."""
    name_hint = _voice_name_hint(voice_id)

    try:
        await search.press("ArrowDown")
        await asyncio.sleep(0.35)
        await search.press("Enter")
        await asyncio.sleep(0.7)
        if await _voice_selection_done(page, voice_id):
            return True
    except Exception:  # noqa: BLE001
        pass

    box = await search.bounding_box()
    if box:
        for offset_y in (72, 100, 130):
            x = box["x"] + min(box["width"] * 0.35, 140)
            y = box["y"] + box["height"] + offset_y
            await page.mouse.click(x, y)
            await asyncio.sleep(0.65)
            if await _voice_selection_done(page, voice_id):
                return True

    for label in (name_hint, voice_id[:10]):
        if not label:
            continue
        try:
            row = (
                page.get_by_text("Select a voice", exact=True)
                .locator("xpath=ancestor::*[1]")
                .locator("div, article, li")
                .filter(has_text=label)
                .filter(has_text=" - ")
                .first
            )
            if await row.count():
                await row.click(force=True, position={"x": 24, "y": 16}, timeout=8_000)
                await asyncio.sleep(0.6)
                if await _voice_selection_done(page, voice_id):
                    return True
        except Exception:  # noqa: BLE001
            continue

    js_ok = await page.evaluate(
        """(vid, nameHint) => {
            const fire = (el) => {
                if (!el) return false;
                el.scrollIntoView({ block: 'center' });
                const o = { bubbles: true, cancelable: true, view: window };
                for (const t of ['pointerdown','mousedown','mouseup','click'])
                    el.dispatchEvent(new MouseEvent(t, o));
                return true;
            };
            const skip = /Select a voice|Explore|My Voices|Language:|Filters|Accent|Category|Search/i;
            const hdr = [...document.querySelectorAll('*')].find(
                el => (el.innerText || '').trim() === 'Select a voice'
            );
            let root = hdr?.closest('aside') || hdr?.parentElement;
            for (let d = 0; d < 14 && root; d++) {
                const cards = [...root.querySelectorAll('div, article, li')].filter(el => {
                    const t = (el.innerText || '').trim();
                    if (t.length < 18 || t.length > 400 || skip.test(t)) return false;
                    if (el.matches('input, textarea')) return false;
                    if (!t.includes(' - ')) return false;
                    const btns = el.querySelectorAll('button');
                    return btns.length >= 1 && btns.length <= 6;
                });
                const pick = cards.find(c => (c.innerText || '').includes(vid))
                    || (nameHint && cards.find(c => (c.innerText || '').toLowerCase().includes(nameHint.toLowerCase())))
                    || cards[0];
                if (!pick) { root = root.parentElement; continue; }
                const buttons = [...pick.querySelectorAll('button')];
                const selectBtn = buttons.find(b => {
                    const al = (b.getAttribute('aria-label') || '').toLowerCase();
                    const t = (b.innerText || '').trim();
                    if (/play|preview|more|menu/i.test(al + t)) return false;
                    return true;
                });
                if (selectBtn && fire(selectBtn)) return true;
                const title = [...pick.querySelectorAll('div, span, p')].find(x => {
                    const t = (x.innerText || '').trim();
                    return t.includes(' - ') && t.length < 80;
                });
                if (fire(title || pick)) return true;
                root = root.parentElement;
            }
            return false;
        }""",
        voice_id,
        name_hint,
    )
    if js_ok and await _voice_selection_done(page, voice_id):
        return True

    try:
        row = page.locator("div, article, li").filter(has_text=" - ").filter(
            has_not_text="Select a voice"
        ).first
        await row.click(force=True, position={"x": 30, "y": 12}, timeout=5_000)
        await asyncio.sleep(0.6)
        return await _voice_selection_done(page, voice_id)
    except Exception:  # noqa: BLE001
        return False


async def _select_voice_by_id(page: Page, voice_id: str) -> None:
    await _select_v3_model(page)
    await _open_voice_panel(page)

    search = await _voice_search_input(page)
    await search.click()
    await search.fill("")
    await search.press_sequentially(voice_id, delay=40)
    logger.info("11Labs: в поиск голоса вставлен id {}", voice_id)
    await asyncio.sleep(2.5)

    for attempt in range(6):
        if await _click_voice_result_card(page, voice_id, search):
            await _dismiss_overlay_panels(page)
            logger.info("11Labs: голос {} выбран (попытка {})", voice_id, attempt + 1)
            return
        logger.warning("11Labs: голос не выбран (попытка {})", attempt + 1)
        await search.click()
        await search.fill(voice_id)
        await asyncio.sleep(1.2)

    raise RuntimeError(f"11Labs: не удалось выбрать голос {voice_id} после поиска")


async def _fill_tts_text(page: Page, input_sel: str, text: str) -> None:
    loc = page.locator(input_sel).first
    await loc.click()
    await loc.fill("")
    if "contenteditable" in input_sel:
        await loc.evaluate(
            """(el, value) => {
                el.focus();
                el.textContent = value;
                el.dispatchEvent(new InputEvent('input', { bubbles: true }));
            }""",
            text,
        )
    else:
        await loc.fill(text)


class ElevenLabsBot:
    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    async def tts(
        self,
        text: str,
        out_path: Path,
        *,
        timeout: float = 300,
        voice_id: str | None = None,
    ) -> Path:
        vid = (voice_id or DEFAULT_ELEVENLABS_VOICE_ID).strip()
        page = await self.session.open_page(settings.elevenlabs_web_url, reuse=True)
        await _ensure_tts_page(page)

        await _select_voice_by_id(page, vid)
        logger.info("11Labs: ждём {:.0f} с после выбора голоса", VOICE_SETUP_WAIT_SEC)
        await asyncio.sleep(VOICE_SETUP_WAIT_SEC)

        input_sel = await _first_visible(page, INPUT_SELECTORS, timeout_ms=45_000)
        if not input_sel:
            await _ensure_tts_page(page)
            input_sel = await _first_visible(page, INPUT_SELECTORS, timeout_ms=20_000)
        if not input_sel:
            raise RuntimeError("11Labs: не найден textarea для текста")

        await _fill_tts_text(page, input_sel, text)
        logger.info("11Labs: текст в поле ({} симв.)", len(text))

        gen_sel = await _first_visible(page, GENERATE_SELECTORS, timeout_ms=15_000)
        if not gen_sel:
            raise RuntimeError("11Labs: не найдена кнопка Generate / Regenerate speech")
        await page.locator(gen_sel).first.click()

        deadline = asyncio.get_event_loop().time() + timeout
        out_path.parent.mkdir(parents=True, exist_ok=True)
        while asyncio.get_event_loop().time() < deadline:
            sel = await _first_visible(page, DOWNLOAD_SELECTORS, timeout_ms=3_000)
            if sel is None:
                await asyncio.sleep(1.0)
                continue
            try:
                async with page.expect_download(timeout=45_000) as dl_info:
                    await page.locator(sel).first.click()
                download = await dl_info.value
                await download.save_as(str(out_path))
                if out_path.stat().st_size < 500:
                    raise RuntimeError(f"11Labs: mp3 слишком мал ({out_path.stat().st_size} B)")
                logger.info("11Labs mp3 saved → {} ({} B)", out_path, out_path.stat().st_size)
                return out_path
            except PWTimeoutError:
                href = await page.locator(sel).first.get_attribute("href")
                if href and ".mp3" in href.lower():
                    resp = await page.context.request.get(href)
                    if resp.status < 400:
                        out_path.write_bytes(await resp.body())
                        logger.info("11Labs mp3 via href → {}", out_path)
                        return out_path
                await asyncio.sleep(1.0)
                continue
        raise PWTimeoutError("11Labs: не дождались загрузки mp3")


async def _recon(text: str) -> None:
    async with browser_session() as bs:
        page = await bs.open_page(settings.elevenlabs_web_url, reuse=True)
        await _ensure_tts_page(page)
        await asyncio.sleep(3)
        info = await page.evaluate(
            """() => {
                const q = sel => Array.from(document.querySelectorAll(sel)).slice(0, 10)
                    .map(el => ({
                        tag: el.tagName, id: el.id,
                        cls: el.className && el.className.toString().slice(0, 120),
                        placeholder: el.getAttribute('placeholder'),
                        ariaLabel: el.getAttribute('aria-label'),
                        text: (el.innerText || '').slice(0, 60),
                    }));
                return {
                    textareas: q('textarea'),
                    inputs: q('input'),
                    buttons: q('button'),
                };
            }"""
        )
        for name, items in info.items():
            logger.info("--- {} ({}) ---", name, len(items))
            for i, it in enumerate(items):
                logger.info("  [{}] {}", i, it)
        if text:
            bot = ElevenLabsBot(bs)
            await bot.tts(text, Path("recon_tts.mp3"))


def _cli() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "recon":
        print("usage: python -m app.bots.elevenlabs recon <text>")
        sys.exit(1)
    asyncio.run(_recon(sys.argv[2]))


if __name__ == "__main__":
    _cli()
