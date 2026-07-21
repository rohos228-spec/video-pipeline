"""5 механик поиска / скачивания / сортировки Outsee → кадры монтажа.

Все пять задействуются на каждый recover:
  1. DOM-scan       — поиск [ID] в тексте вокруг thumb, сорт по Y (сверху=новее)
  2. Click-panel    — клик thumb → ID в панели, сорт по индексу галереи
  3. get_by_text    — поиск видимого [ID] на странице, сорт по длине матча
  4. URL-timestamp  — поиск по basename/thumb URL, сорт по ts в имени файла
  5. Pending-prio   — сорт: pending кадры первыми, затем frame, затем freshness

Скачивание (5 путей, пока файл не валиден):
  D1 expect_download по кнопке у thumb/карточки
  D2 expect_download в панели «Результат»
  D3 context.request реального full PNG из DOM (своя подпись)
  D4 page.evaluate fetch(arrayBuffer) того же full URL (cookies браузера)
  D5 cold cascade download_saved_image_by_prompt_id
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from app.bots.outsee import (
    _MIN_IMAGE_BYTES,
    _find_result_panel_card,
    _is_outsee_thumb_url,
    _outsee_image_stable_key,
    _physical_mouse_click,
    _strip_url_query,
    _validate_downloaded_image,
    download_saved_image_by_prompt_id,
)
from app.generation_options import build_gen_id_prefix

_ID_RE = re.compile(
    r"\[ID:\s*P(\d+)-F(\d+)-([a-f0-9]{8})\](-S2)?",
    re.IGNORECASE,
)
_TS_IN_NAME_RE = re.compile(r"image_(\d+)_", re.I)
_READY = _MIN_IMAGE_BYTES


@dataclass
class HitCandidate:
    """Единый кандидат после любой механики поиска."""

    frame_number: int
    shot: int
    short_uuid: str
    prompt_id_prefix: str
    img_src: str
    sources: set[str] = field(default_factory=set)
    gallery_index: int = 10_000
    dom_y: float = 10_000.0
    url_ts: int = 0
    text_score: int = 0
    pending_boost: int = 0

    @property
    def key(self) -> tuple[int, int]:
        return (self.frame_number, self.shot)

    @property
    def dedupe(self) -> str:
        return f"{self.frame_number}:{self.shot}:{self.short_uuid}"


def _parse_ids(text: str, *, project_id: int) -> list[tuple[str, int, int, str]]:
    out: list[tuple[str, int, int, str]] = []
    seen: set[str] = set()
    for m in _ID_RE.finditer(text or ""):
        pid = int(m.group(1))
        if pid != int(project_id):
            continue
        frame = int(m.group(2))
        hex8 = m.group(3).lower()
        shot = 2 if m.group(4) else 1
        prefix = build_gen_id_prefix(pid, frame, hex8)
        if shot == 2:
            prefix = prefix + "-S2"
        if prefix in seen:
            continue
        seen.add(prefix)
        out.append((prefix, frame, shot, hex8))
    return out


def _url_ts(src: str) -> int:
    name = Path(_strip_url_query(src)).name
    m = _TS_IN_NAME_RE.search(name)
    return int(m.group(1)) if m else 0


def _merge_hit(
    bag: dict[str, HitCandidate],
    *,
    project_id: int,
    prefix: str,
    frame: int,
    shot: int,
    hex8: str,
    img_src: str,
    source: str,
    gallery_index: int = 10_000,
    dom_y: float = 10_000.0,
    text_score: int = 0,
) -> None:
    dedupe = f"{frame}:{shot}:{hex8}"
    h = bag.get(dedupe)
    if h is None:
        bag[dedupe] = HitCandidate(
            frame_number=frame,
            shot=shot,
            short_uuid=hex8,
            prompt_id_prefix=prefix,
            img_src=img_src,
            sources={source},
            gallery_index=gallery_index,
            dom_y=dom_y,
            url_ts=_url_ts(img_src),
            text_score=text_score,
        )
        return
    h.sources.add(source)
    if img_src and (not h.img_src or _url_ts(img_src) >= h.url_ts):
        h.img_src = img_src
        h.url_ts = max(h.url_ts, _url_ts(img_src))
    h.gallery_index = min(h.gallery_index, gallery_index)
    h.dom_y = min(h.dom_y, dom_y)
    h.text_score = max(h.text_score, text_score)


# ---------------------------------------------------------------------------
# ПОИСК — механика 1: DOM-scan
# ---------------------------------------------------------------------------
async def search_m1_dom_scan(
    page, project_id: int, *, limit: int = 40
) -> list[HitCandidate]:
    js = """
    ([projectId, limit]) => {
        const needle = '[ID: P' + projectId + '-F';
        const rows = [];
        for (const img of document.querySelectorAll('img')) {
            const r = img.getBoundingClientRect();
            if (r.width < 180 || r.height < 180 || !img.src) continue;
            let cur = img, text = '';
            for (let i = 0; i < 14 && cur; i++) {
                text += '\\n' + (cur.innerText || cur.textContent || '');
                if (cur.tagName === 'TEXTAREA' || cur.tagName === 'INPUT')
                    text += '\\n' + (cur.value || '');
                cur = cur.parentElement;
            }
            if (!text.includes(needle)) continue;
            rows.push({ src: img.src, text: text.slice(0, 8000), y: r.top, idx: rows.length });
            if (rows.length >= limit) break;
        }
        return rows;
    }
    """
    bag: dict[str, HitCandidate] = {}
    try:
        rows = await page.evaluate(js, [int(project_id), int(limit)])
    except Exception as e:  # noqa: BLE001
        logger.warning("search_m1_dom_scan: {}", e)
        return []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        src = str(row.get("src") or "")
        text = str(row.get("text") or "")
        y = float(row.get("y") or 10_000)
        idx = int(row.get("idx") or 10_000)
        for prefix, frame, shot, hex8 in _parse_ids(text, project_id=project_id):
            _merge_hit(
                bag,
                project_id=project_id,
                prefix=prefix,
                frame=frame,
                shot=shot,
                hex8=hex8,
                img_src=src,
                source="m1_dom",
                gallery_index=idx,
                dom_y=y,
                text_score=len(text),
            )
    logger.info("search_m1_dom_scan: {} hits", len(bag))
    return list(bag.values())


# ---------------------------------------------------------------------------
# ПОИСК — механика 2: click-panel
# ---------------------------------------------------------------------------
async def search_m2_click_panel(
    page,
    project_id: int,
    *,
    limit: int = 24,
    project_db_id: int | None = None,
) -> list[HitCandidate]:
    from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

    srcs = await page.evaluate(
        """(limit) => {
            const out = [];
            for (const img of document.querySelectorAll('img')) {
                const r = img.getBoundingClientRect();
                if (r.width >= 180 && r.height >= 180 && img.src)
                    out.push({ src: img.src, y: r.top });
            }
            out.sort((a, b) => a.y - b.y);
            return out.slice(0, limit);
        }""",
        int(limit),
    )
    bag: dict[str, HitCandidate] = {}
    for idx, row in enumerate(srcs or []):
        if not isinstance(row, dict):
            continue
        src = str(row.get("src") or "")
        y = float(row.get("y") or 10_000)
        if not src:
            continue
        abort_if_cancelled(project_db_id)
        try:
            loc = page.locator(f'img[src="{src}"]').first
            if await loc.count() == 0:
                base = Path(src.split("?", 1)[0]).name
                loc = page.locator(f'img[src*="{base}"]').first
            if await loc.count() == 0:
                continue
            await _physical_mouse_click(
                page, loc, project_id=project_db_id, label=f"m2#{idx}", prefer_cdp=True
            )
            await sleep_cancellable(0.4, project_db_id)
            panel_text = await page.evaluate(
                """() => {
                    let best = '';
                    for (const el of document.querySelectorAll(
                        'section, aside, div[role="dialog"], textarea, [contenteditable="true"], p, pre'
                    )) {
                        const t = (el.value || el.innerText || el.textContent || '').trim();
                        if (t.includes('[ID:') && t.length > best.length && t.length < 20000)
                            best = t;
                    }
                    if (!best.includes('[ID:')) {
                        const body = (document.body && document.body.innerText) || '';
                        const i = body.indexOf('[ID:');
                        if (i >= 0) best = body.slice(Math.max(0, i - 40), i + 400);
                    }
                    return best;
                }"""
            )
            for prefix, frame, shot, hex8 in _parse_ids(
                str(panel_text or ""), project_id=project_id
            ):
                _merge_hit(
                    bag,
                    project_id=project_id,
                    prefix=prefix,
                    frame=frame,
                    shot=shot,
                    hex8=hex8,
                    img_src=src,
                    source="m2_click",
                    gallery_index=idx,
                    dom_y=y,
                    text_score=len(str(panel_text or "")),
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("search_m2_click #{}: {}", idx, e)
            continue
    logger.info("search_m2_click_panel: {} hits", len(bag))
    return list(bag.values())


# ---------------------------------------------------------------------------
# ПОИСК — механика 3: get_by_text / body scan
# ---------------------------------------------------------------------------
async def search_m3_get_by_text(page, project_id: int) -> list[HitCandidate]:
    bag: dict[str, HitCandidate] = {}
    try:
        body = await page.evaluate(
            "() => (document.body && (document.body.innerText || '')) || ''"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("search_m3_get_by_text body: {}", e)
        body = ""
    try:
        values = await page.evaluate(
            """() => Array.from(document.querySelectorAll('textarea, input, [contenteditable="true"]'))
                .map(el => el.value || el.innerText || '')
                .filter(Boolean)
                .join('\\n')"""
        )
    except Exception:  # noqa: BLE001
        values = ""
    blob = f"{body}\n{values}"
    # Привязка к ближайшему большому img по порядку появления ID в тексте —
    # берём верхние big imgs как кандидаты src.
    try:
        top_srcs = await page.evaluate(
            """() => {
                const out = [];
                for (const img of document.querySelectorAll('img')) {
                    const r = img.getBoundingClientRect();
                    if (r.width >= 180 && r.height >= 180 && img.src)
                        out.push({ src: img.src, y: r.top });
                }
                out.sort((a, b) => a.y - b.y);
                return out.slice(0, 30);
            }"""
        )
    except Exception:  # noqa: BLE001
        top_srcs = []
    parsed = _parse_ids(blob, project_id=project_id)
    for i, (prefix, frame, shot, hex8) in enumerate(parsed):
        src = ""
        y = 10_000.0
        if top_srcs and i < len(top_srcs) and isinstance(top_srcs[i], dict):
            src = str(top_srcs[i].get("src") or "")
            y = float(top_srcs[i].get("y") or 10_000)
        elif top_srcs and isinstance(top_srcs[0], dict):
            src = str(top_srcs[0].get("src") or "")
            y = float(top_srcs[0].get("y") or 10_000)
        _merge_hit(
            bag,
            project_id=project_id,
            prefix=prefix,
            frame=frame,
            shot=shot,
            hex8=hex8,
            img_src=src,
            source="m3_text",
            gallery_index=i,
            dom_y=y,
            text_score=len(prefix) + 100,
        )
    logger.info("search_m3_get_by_text: {} hits", len(bag))
    return list(bag.values())


# ---------------------------------------------------------------------------
# ПОИСК — механика 4: URL / basename / timestamp
# ---------------------------------------------------------------------------
async def search_m4_url_timestamp(
    page, project_id: int, *, known_srcs: list[str] | None = None
) -> list[HitCandidate]:
    """Собирает big imgs, сортирует по ts в имени; ID — из data/alt/title/nearby."""
    js = """
    ([projectId]) => {
        const needle = 'P' + projectId + '-F';
        const out = [];
        for (const img of document.querySelectorAll('img')) {
            const r = img.getBoundingClientRect();
            if (r.width < 180 || r.height < 180 || !img.src) continue;
            const attrs = [img.alt || '', img.title || '', img.getAttribute('aria-label') || ''].join(' ');
            let nearby = attrs;
            let cur = img.parentElement;
            for (let i = 0; i < 4 && cur; i++) {
                nearby += '\\n' + (cur.innerText || '').slice(0, 500);
                cur = cur.parentElement;
            }
            out.push({
                src: img.src,
                y: r.top,
                nearby: nearby.slice(0, 4000),
                hasNeedle: nearby.includes(needle) || attrs.includes(needle),
            });
        }
        return out;
    }
    """
    bag: dict[str, HitCandidate] = {}
    try:
        rows = await page.evaluate(js, [int(project_id)])
    except Exception as e:  # noqa: BLE001
        logger.warning("search_m4_url_timestamp: {}", e)
        rows = []
    # Добавим known_srcs как «якорные»
    extra = [{"src": s, "y": 0, "nearby": "", "hasNeedle": False} for s in (known_srcs or [])]
    rows = list(rows or []) + extra
    rows.sort(key=lambda r: -_url_ts(str((r or {}).get("src") or "")))
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        src = str(row.get("src") or "")
        if not src:
            continue
        nearby = str(row.get("nearby") or "")
        parsed = _parse_ids(nearby, project_id=project_id)
        if not parsed and not row.get("hasNeedle"):
            # Без ID в nearby — всё равно регистрируем как URL-якорь позже не пройдёт
            # без prefix; пропускаем.
            continue
        for prefix, frame, shot, hex8 in parsed:
            _merge_hit(
                bag,
                project_id=project_id,
                prefix=prefix,
                frame=frame,
                shot=shot,
                hex8=hex8,
                img_src=src,
                source="m4_url",
                gallery_index=idx,
                dom_y=float(row.get("y") or 10_000),
                text_score=50 + (20 if row.get("hasNeedle") else 0),
            )
    logger.info("search_m4_url_timestamp: {} hits", len(bag))
    return list(bag.values())


# ---------------------------------------------------------------------------
# СОРТИРОВКА — механика 5: pending priority + freshness
# ---------------------------------------------------------------------------
def sort_m5_pending_priority(
    hits: list[HitCandidate],
    *,
    frame_filter: set[tuple[int, int]] | None,
    pending_keys: set[tuple[int, int]] | None = None,
) -> list[HitCandidate]:
    """Сортировка кандидатов: pending → filter → свежесть (ts, -y, index)."""
    pending_keys = pending_keys or set()
    for h in hits:
        h.pending_boost = 0
        if h.key in pending_keys:
            h.pending_boost += 1000
        if frame_filter is not None and h.key in frame_filter:
            h.pending_boost += 500
        # Больше источников поиска = выше доверие
        h.pending_boost += 10 * len(h.sources)

    def sort_key(h: HitCandidate) -> tuple:
        # больше boost / ts лучше; меньше y/index лучше
        return (
            -h.pending_boost,
            -h.url_ts,
            h.dom_y,
            h.gallery_index,
            -h.text_score,
            h.frame_number,
            h.shot,
        )

    filtered = hits
    if frame_filter is not None:
        filtered = [h for h in hits if h.key in frame_filter]
    filtered = sorted(filtered, key=sort_key)
    # Один hit на (frame, shot) — первый после сортировки = лучший
    chosen: dict[tuple[int, int], HitCandidate] = {}
    for h in filtered:
        if h.key not in chosen:
            chosen[h.key] = h
    ordered = list(chosen.values())
    ordered.sort(key=sort_key)
    logger.info(
        "sort_m5_pending_priority: in={} out={} filter={}",
        len(hits),
        len(ordered),
        sorted(frame_filter) if frame_filter else "all",
    )
    return ordered


# ---------------------------------------------------------------------------
# СКАЧИВАНИЕ — 5 путей
# ---------------------------------------------------------------------------
def _ready(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= _READY
    except OSError:
        return False


async def _click_thumb(page, img_src: str, *, project_id: int | None) -> bool:
    if not img_src:
        return False
    base = Path(_strip_url_query(img_src)).name
    for fragment in (base, _strip_url_query(img_src)):
        if not fragment:
            continue
        loc = page.locator(f'img[src*="{fragment}"]').first
        if await loc.count() == 0:
            continue
        try:
            await _physical_mouse_click(
                page, loc, project_id=project_id, label="five-thumb", prefer_cdp=True
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("_click_thumb {}: {}", fragment[-40:], e)
    return False


async def _expect_download_click(page, btn, out_path: Path, *, project_id: int | None) -> bool:
    from app.services.step_cancel import await_with_cancel

    try:
        with contextlib_suppress():
            await btn.scroll_into_view_if_needed(timeout=2000)
        async with page.expect_download(timeout=45_000) as dl_info:
            await _physical_mouse_click(
                page, btn, project_id=project_id, label="five-dl"
            )
        download = await dl_info.value
        await await_with_cancel(download.save_as(str(out_path)), project_id)
        return _ready(out_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("expect_download_click: {}", e)
        return False


class contextlib_suppress:
    """Локальный suppress без import cycle в hot path."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return True


async def download_d1_thumb_button(
    page, hit: HitCandidate, out_path: Path, *, project_id: int | None
) -> bool:
    """D1: клик thumb → Download в ancestor карточки."""
    await _click_thumb(page, hit.img_src, project_id=project_id)
    await page.wait_for_timeout(400)
    base = Path(_strip_url_query(hit.img_src)).name if hit.img_src else ""
    if not base:
        return False
    img = page.locator(f'img[src*="{base}"]').first
    if await img.count() == 0:
        return False
    card = img.locator(
        "xpath=ancestor::*[descendant::button"
        "[descendant::svg[contains(@class,'lucide-download')]]][1]"
    )
    if await card.count() == 0:
        return False
    btn = card.locator("button:has(svg.lucide-download)").first
    if await btn.count() == 0:
        return False
    with contextlib_suppress():
        await card.hover(timeout=2000)
    ok = await _expect_download_click(page, btn, out_path, project_id=project_id)
    if ok:
        logger.info("download_d1_thumb_button: OK {}", out_path.name)
    return ok


async def download_d2_result_panel(
    page, hit: HitCandidate, out_path: Path, *, project_id: int | None
) -> bool:
    """D2: клик thumb → Download в панели «Результат»."""
    await _click_thumb(page, hit.img_src, project_id=project_id)
    await page.wait_for_timeout(500)
    panel = await _find_result_panel_card(page, hit.img_src or None)
    btn = None
    if panel is not None:
        btn = panel.locator("button:has(svg.lucide-download)").first
        if await btn.count() == 0:
            btn = panel.locator(
                "button:has-text('Скачать'), button:has-text('Download')"
            ).first
    if btn is None or await btn.count() == 0:
        btn = page.locator("button:has(svg.lucide-download)").first
    if await btn.count() == 0:
        return False
    ok = await _expect_download_click(page, btn, out_path, project_id=project_id)
    if ok:
        logger.info("download_d2_result_panel: OK {}", out_path.name)
    return ok


async def _best_dom_full_url(page, hit: HitCandidate) -> str | None:
    key = _outsee_image_stable_key(hit.img_src) if hit.img_src else ""
    try:
        urls = await page.evaluate(
            """(key) => {
                const out = [];
                for (const img of document.querySelectorAll('img')) {
                    const s = img.currentSrc || img.src || '';
                    if (!s) continue;
                    const r = img.getBoundingClientRect();
                    out.push({
                        src: s,
                        w: r.width,
                        h: r.height,
                        area: Math.max(0, r.width) * Math.max(0, r.height),
                        isThumb: s.toLowerCase().includes('_thumb'),
                        hasKey: key ? s.toLowerCase().includes(key.toLowerCase()) : false,
                        isPng: s.toLowerCase().includes('.png'),
                    });
                }
                out.sort((a, b) => {
                    // non-thumb png with key first, then area
                    const sa = (a.isThumb ? 1000 : 0) + (a.isPng ? 0 : 50) + (a.hasKey ? 0 : 20);
                    const sb = (b.isThumb ? 1000 : 0) + (b.isPng ? 0 : 50) + (b.hasKey ? 0 : 20);
                    if (sa !== sb) return sa - sb;
                    return b.area - a.area;
                });
                return out.map(x => x.src);
            }""",
            key,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("_best_dom_full_url: {}", e)
        return None
    for u in urls or []:
        if not isinstance(u, str) or not u:
            continue
        if _is_outsee_thumb_url(u):
            continue
        if ".png" in u.lower() or "signature=" in u.lower():
            return u
    for u in urls or []:
        if isinstance(u, str) and u and not _is_outsee_thumb_url(u):
            return u
    return None


async def download_d3_dom_full_request(
    page, hit: HitCandidate, out_path: Path, *, project_id: int | None
) -> bool:
    """D3: полный PNG из DOM (своя подпись) через context.request."""
    from app.bots.outsee import _download_via_context

    await _click_thumb(page, hit.img_src, project_id=project_id)
    await page.wait_for_timeout(400)
    url = await _best_dom_full_url(page, hit)
    if not url:
        return False
    try:
        await _download_via_context(page, url, out_path, project_id=project_id, attempts=2)
        _validate_downloaded_image(out_path, gen_id=hit.short_uuid, img_url=url)
        logger.info("download_d3_dom_full_request: OK {} ← {}", out_path.name, url[:80])
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("download_d3_dom_full_request: {}", e)
        return False


async def download_d4_page_fetch(
    page, hit: HitCandidate, out_path: Path, *, project_id: int | None
) -> bool:
    """D4: fetch в контексте страницы (cookies) → bytes на диск."""
    await _click_thumb(page, hit.img_src, project_id=project_id)
    await page.wait_for_timeout(400)
    url = await _best_dom_full_url(page, hit) or hit.img_src
    if not url:
        return False
    try:
        b64 = await page.evaluate(
            """async (url) => {
                const resp = await fetch(url, { credentials: 'include', mode: 'cors' });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const buf = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let binary = '';
                const chunk = 0x8000;
                for (let i = 0; i < bytes.length; i += chunk) {
                    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                }
                return btoa(binary);
            }""",
            url,
        )
        if not isinstance(b64, str) or not b64:
            return False
        import base64

        raw = base64.b64decode(b64)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw)
        _validate_downloaded_image(out_path, gen_id=hit.short_uuid, img_url=url)
        logger.info("download_d4_page_fetch: OK {} ({} B)", out_path.name, len(raw))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("download_d4_page_fetch: {}", e)
        return False


async def download_d5_cascade(
    page, hit: HitCandidate, out_path: Path, *, project_id: int | None
) -> bool:
    """D5: полный card-click cascade по [ID]."""
    try:
        await download_saved_image_by_prompt_id(
            page,
            prompt_id_prefix=hit.prompt_id_prefix,
            out_path=out_path,
            project_id=project_id,
            gen_id=hit.short_uuid,
        )
        ok = _ready(out_path)
        if ok:
            logger.info("download_d5_cascade: OK {}", out_path.name)
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning("download_d5_cascade: {}", e)
        return False


DOWNLOAD_MECHANICS = (
    ("d1_thumb_button", download_d1_thumb_button),
    ("d2_result_panel", download_d2_result_panel),
    ("d3_dom_full_request", download_d3_dom_full_request),
    ("d4_page_fetch", download_d4_page_fetch),
    ("d5_cascade", download_d5_cascade),
)


async def download_with_all_mechanics(
    page,
    hit: HitCandidate,
    out_path: Path,
    *,
    project_id: int | None,
) -> tuple[bool, str]:
    """Пробует D1→D5 пока файл не валиден. Возвращает (ok, mechanic_name)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for name, fn in DOWNLOAD_MECHANICS:
        if _ready(out_path):
            return True, name
        try:
            if out_path.exists() and out_path.stat().st_size < _READY:
                out_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            ok = await fn(page, hit, out_path, project_id=project_id)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}:{e}")
            ok = False
        if ok and _ready(out_path):
            try:
                _validate_downloaded_image(
                    out_path, gen_id=hit.short_uuid, img_url=hit.img_src
                )
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}:validate:{e}")
                try:
                    out_path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            return True, name
        errors.append(f"{name}:fail")
    logger.error(
        "download_with_all_mechanics FAILED F{} shot{}: {}",
        hit.frame_number,
        hit.shot,
        "; ".join(errors[-8:]),
    )
    return False, "none"


async def run_five_mechanics_search(
    page,
    project_id: int,
    *,
    frame_filter: set[tuple[int, int]] | None = None,
    pending_keys: set[tuple[int, int]] | None = None,
    project_db_id: int | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    """Задействует все 5 механик поиска+сортировки. Возвращает выбранные hits."""
    m1 = await search_m1_dom_scan(page, project_id, limit=limit)
    m2 = await search_m2_click_panel(
        page, project_id, limit=min(24, limit), project_db_id=project_db_id
    )
    m3 = await search_m3_get_by_text(page, project_id)
    known = [h.img_src for h in (m1 + m2) if h.img_src]
    m4 = await search_m4_url_timestamp(page, project_id, known_srcs=known)

    bag: dict[str, HitCandidate] = {}
    for group, label in (
        (m1, "m1_dom"),
        (m2, "m2_click"),
        (m3, "m3_text"),
        (m4, "m4_url"),
    ):
        for h in group:
            existing = bag.get(h.dedupe)
            if existing is None:
                bag[h.dedupe] = h
                continue
            existing.sources |= h.sources | {label}
            if h.url_ts >= existing.url_ts and h.img_src:
                existing.img_src = h.img_src
                existing.url_ts = h.url_ts
            existing.gallery_index = min(existing.gallery_index, h.gallery_index)
            existing.dom_y = min(existing.dom_y, h.dom_y)
            existing.text_score = max(existing.text_score, h.text_score)

    merged = list(bag.values())
    # Механика 5 — сортировка
    ordered = sort_m5_pending_priority(
        merged, frame_filter=frame_filter, pending_keys=pending_keys
    )
    return {
        "hits": ordered,
        "stats": {
            "m1_dom": len(m1),
            "m2_click": len(m2),
            "m3_text": len(m3),
            "m4_url": len(m4),
            "m5_sorted": len(ordered),
            "merged": len(merged),
            "mechanics_used": ["m1_dom", "m2_click", "m3_text", "m4_url", "m5_sort"],
        },
    }
