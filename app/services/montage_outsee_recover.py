"""Принудительно забрать готовые картинки из истории Outsee в кадры монтажа.

Пользователь уже оплатил Generate — карточки лежат в галерее outsee, а локально
файла нет (валидация/download упали). Сканируем последние thumb'ы, достаём
`[ID: P{project}-F{frame}-…]`, качаем и finaliz'им кадр.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chrome_cdp import fetch_cdp_version
from app.bots.outsee import (
    OutseeBot,
    _GALLERY_ID_SCAN_LIMIT,
    _image_page_url,
    _wait_gallery_thumbs,
)
from app.generation_options import (
    DEFAULTS,
    IMAGE_GENERATORS_BY_ID,
    build_gen_id_prefix,
)
from app.models import Project
from app.services.montage_board_assets import finalize_scene_image
from app.services.montage_board_meta import add_highlight, montage_meta, set_montage_meta
from app.services.outsee_lane import outsee_lane
from app.services.plan_shot2 import find_shot1_image, find_shot2_image
from app.settings import settings

_READY_BYTES = 200_000
_ID_IN_TEXT_RE = re.compile(
    r"\[ID:\s*P(\d+)-F(\d+)-([a-f0-9]{8})\](-S2)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GalleryHit:
    project_id: int
    frame_number: int
    shot: int
    short_uuid: str
    prompt_id_prefix: str
    img_src: str


def _ready_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_file() and path.stat().st_size >= _READY_BYTES
    except OSError:
        return False


def _frame_needs_image(project: Project, frame_number: int, shot: int) -> bool:
    scenes = project.data_dir / "scenes"
    if shot == 2:
        return not _ready_file(find_shot2_image(scenes, frame_number))
    return not _ready_file(find_shot1_image(scenes, frame_number))


def _parse_ids_from_text(text: str, *, project_id: int) -> list[tuple[str, int, int, str]]:
    """→ list of (full_prefix, frame, shot, hex)."""
    out: list[tuple[str, int, int, str]] = []
    seen: set[str] = set()
    for m in _ID_IN_TEXT_RE.finditer(text or ""):
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


async def scan_gallery_hits_for_project(
    page,
    project_id: int,
    *,
    limit: int = _GALLERY_ID_SCAN_LIMIT,
) -> list[GalleryHit]:
    """Пассивный скан: `[ID]` в предках последних больших thumb'ов."""
    js = """
    ([projectId, limit, maxLevels]) => {
        const needle = '[ID: P' + projectId + '-F';
        const bigImgs = [];
        for (const img of document.querySelectorAll('img')) {
            const r = img.getBoundingClientRect();
            if (r.width >= 180 && r.height >= 180 && img.src) {
                bigImgs.push(img);
            }
        }
        const out = [];
        for (const img of bigImgs.slice(0, limit)) {
            let cur = img;
            let text = '';
            for (let i = 0; i < maxLevels && cur; i++) {
                text += '\\n' + (cur.innerText || cur.textContent || '');
                const tag = cur.tagName && cur.tagName.toLowerCase();
                if (tag === 'textarea' || tag === 'input') {
                    text += '\\n' + (cur.value || '');
                }
                cur = cur.parentElement;
            }
            if (!text.includes(needle)) continue;
            out.push({ src: img.src, text: text.slice(0, 8000) });
        }
        return out;
    }
    """
    try:
        rows = await page.evaluate(js, [int(project_id), int(limit), 14])
    except Exception as e:  # noqa: BLE001
        logger.warning("scan_gallery_hits_for_project evaluate: {}", e)
        return []

    hits: list[GalleryHit] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        src = str(row.get("src") or "")
        text = str(row.get("text") or "")
        if not src:
            continue
        for prefix, frame, shot, hex8 in _parse_ids_from_text(text, project_id=project_id):
            key = f"{frame}:{shot}:{hex8}"
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                GalleryHit(
                    project_id=project_id,
                    frame_number=frame,
                    shot=shot,
                    short_uuid=hex8,
                    prompt_id_prefix=prefix,
                    img_src=src,
                )
            )
    return hits


async def scan_gallery_hits_by_clicking(
    page,
    project_id: int,
    *,
    limit: int = 25,
    project_db_id: int | None = None,
) -> list[GalleryHit]:
    """Клик по последним thumb'ам — ID часто только в правой панели."""
    from app.services.step_cancel import abort_if_cancelled, sleep_cancellable

    srcs = await page.evaluate(
        """(limit) => {
            const out = [];
            for (const img of document.querySelectorAll('img')) {
                const r = img.getBoundingClientRect();
                if (r.width >= 180 && r.height >= 180 && img.src) out.push(img.src);
            }
            return out.slice(0, limit);
        }""",
        int(limit),
    )
    hits: list[GalleryHit] = []
    seen: set[str] = set()
    for src in srcs or []:
        if not isinstance(src, str) or not src:
            continue
        abort_if_cancelled(project_db_id)
        try:
            loc = page.locator(f'img[src="{src}"]').first
            if await loc.count() == 0:
                # src мог смениться query — пробуем basename
                base = Path(src.split("?", 1)[0]).name
                loc = page.locator(f'img[src*="{base}"]').first
            if await loc.count() == 0:
                continue
            await loc.click(timeout=4000)
            await sleep_cancellable(0.45, project_db_id)
            panel_text = await page.evaluate(
                """() => {
                    const midX = window.innerWidth * 0.35;
                    let best = '';
                    for (const el of document.querySelectorAll(
                        'section, aside, div[role="dialog"], textarea, [contenteditable="true"]'
                    )) {
                        const r = el.getBoundingClientRect();
                        if (r.left < midX || r.width < 60) continue;
                        const t = (el.value || el.innerText || el.textContent || '').trim();
                        if (t.length > best.length && t.length < 15000) best = t;
                    }
                    return best;
                }"""
            )
            for prefix, frame, shot, hex8 in _parse_ids_from_text(
                str(panel_text or ""), project_id=project_id
            ):
                key = f"{frame}:{shot}:{hex8}"
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    GalleryHit(
                        project_id=project_id,
                        frame_number=frame,
                        shot=shot,
                        short_uuid=hex8,
                        prompt_id_prefix=prefix,
                        img_src=src,
                    )
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("scan_gallery click {}: {}", src[:80], e)
            continue
    return hits


def _dest_path(project: Project, hit: GalleryHit) -> Path:
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    if hit.shot == 2:
        return scenes / f"frame_{hit.frame_number:03d}_s2_{hit.short_uuid}.png"
    return scenes / f"frame_{hit.frame_number:03d}_{hit.short_uuid}.png"


async def _download_hit(
    page,
    project: Project,
    hit: GalleryHit,
) -> Path | None:
    """Скачивание тем же путём, что img-шаг: card-click cascade без img_url."""
    from app.bots.outsee import download_saved_image_by_prompt_id

    dest = _dest_path(project, hit)
    if _ready_file(dest):
        return dest
    try:
        await download_saved_image_by_prompt_id(
            page,
            prompt_id_prefix=hit.prompt_id_prefix,
            out_path=dest,
            project_id=project.id,
            gen_id=hit.short_uuid,
        )
        return dest
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "montage outsee recover download P{} F{} shot{}: {}",
            project.id,
            hit.frame_number,
            hit.shot,
            e,
        )
        return None if not _ready_file(dest) else dest


async def recover_montage_images_from_outsee(
    session: AsyncSession,
    project: Project,
    *,
    frame_filter: set[tuple[int, int]] | None = None,
    click_scan: bool = True,
    limit: int = _GALLERY_ID_SCAN_LIMIT,
) -> dict[str, Any]:
    """Сканирует историю Outsee и сохраняет недостающие кадры монтажа.

    frame_filter: optional set of (frame_number, shot). None = все hit'ы проекта,
    у которых локально нет готового файла.
    """
    try:
        await fetch_cdp_version(settings.browser_cdp_url)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Chrome CDP :29229 не отвечает — запустите Start-Chrome.cmd и откройте outsee.io"
        ) from exc

    img_gen = IMAGE_GENERATORS_BY_ID.get(
        project.image_generator or DEFAULTS["image_generator"]
    )
    model_slug = img_gen.outsee_slug if img_gen else None

    saved: list[dict[str, Any]] = []
    skipped: list[str] = []
    errors: list[str] = []
    hits: list[GalleryHit] = []

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        async with outsee_lane(project_id=project.id, op="montage_force_history"):
            page = await outsee.session.open_page(
                _image_page_url(model_slug), reuse=True
            )
            await _wait_gallery_thumbs(
                page, min_count=1, timeout_s=30.0, project_id=project.id
            )
            hits = await scan_gallery_hits_for_project(
                page, project.id, limit=limit
            )
            if click_scan and len(hits) < 3:
                clicked = await scan_gallery_hits_by_clicking(
                    page,
                    project.id,
                    limit=min(25, limit),
                    project_db_id=project.id,
                )
                seen = {
                    f"{h.frame_number}:{h.shot}:{h.short_uuid}" for h in hits
                }
                for h in clicked:
                    key = f"{h.frame_number}:{h.shot}:{h.short_uuid}"
                    if key not in seen:
                        hits.append(h)
                        seen.add(key)

            logger.info(
                "montage outsee recover #{}: {} gallery hits (filter={})",
                project.id,
                len(hits),
                sorted(frame_filter) if frame_filter else "missing-only",
            )

            chosen: dict[tuple[int, int], GalleryHit] = {}
            for hit in hits:
                key = (hit.frame_number, hit.shot)
                if frame_filter is not None and key not in frame_filter:
                    continue
                if key in chosen:
                    continue
                if not _frame_needs_image(project, hit.frame_number, hit.shot):
                    skipped.append(f"{hit.frame_number}:{hit.shot}=already")
                    continue
                chosen[key] = hit

            board = montage_meta(project)
            for key, hit in chosen.items():
                path = await _download_hit(page, project, hit)
                if path is None or not _ready_file(path):
                    errors.append(
                        f"F{hit.frame_number} shot{hit.shot}: download failed"
                    )
                    continue
                try:
                    await finalize_scene_image(
                        session,
                        project,
                        hit.frame_number,
                        shot=hit.shot,
                        new_path=path,
                    )
                    add_highlight(board, f"{hit.frame_number}:image{hit.shot}")
                    saved.append(
                        {
                            "frame_number": hit.frame_number,
                            "shot": hit.shot,
                            "path": str(path),
                            "prompt_id_prefix": hit.prompt_id_prefix,
                        }
                    )
                    logger.info(
                        "montage outsee recover #{} saved F{} shot{} → {}",
                        project.id,
                        hit.frame_number,
                        hit.shot,
                        path.name,
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(
                        f"F{hit.frame_number} shot{hit.shot}: finalize {e}"
                    )

            if saved:
                pending = list(board.get("pending_ops") or [])
                saved_keys = {
                    (int(s["frame_number"]), int(s["shot"])) for s in saved
                }
                new_pending = []
                for op in pending:
                    try:
                        fn = int(op.get("frame_number"))
                        sh = int(op.get("shot") or 1)
                    except (TypeError, ValueError):
                        new_pending.append(op)
                        continue
                    t = str(op.get("type") or "")
                    if t.startswith("image") and (fn, sh) in saved_keys:
                        continue
                    new_pending.append(op)
                board["pending_ops"] = new_pending
                set_montage_meta(project, board)
                await session.flush()

    return {
        "ok": not errors,
        "saved": saved,
        "saved_count": len(saved),
        "skipped": skipped,
        "errors": errors,
        "hits_scanned": len(hits),
    }


async def recover_before_regen_ops(
    session: AsyncSession,
    project: Project,
    ops: list[dict[str, Any]],
) -> dict[str, Any]:
    """Перед Generate: забрать из истории Outsee кадры из image-очереди."""
    frame_filter: set[tuple[int, int]] = set()
    for op in ops:
        t = str(op.get("type") or "")
        if not t.startswith("image"):
            continue
        try:
            frame_filter.add((int(op["frame_number"]), int(op.get("shot") or 1)))
        except (KeyError, TypeError, ValueError):
            continue
    if not frame_filter:
        return {"ok": True, "saved": [], "saved_count": 0, "skipped_ops": ops}

    result = await recover_montage_images_from_outsee(
        session, project, frame_filter=frame_filter, click_scan=True
    )
    saved_keys = {
        (int(s["frame_number"]), int(s["shot"])) for s in result.get("saved") or []
    }
    remaining = []
    for op in ops:
        t = str(op.get("type") or "")
        if t.startswith("image"):
            try:
                key = (int(op["frame_number"]), int(op.get("shot") or 1))
            except (KeyError, TypeError, ValueError):
                remaining.append(op)
                continue
            if key in saved_keys:
                continue
        remaining.append(op)
    result["remaining_ops"] = remaining
    result["removed_ops"] = len(ops) - len(remaining)
    return result


def rebuild_prefix_from_filename(project_id: int, path: Path) -> str | None:
    """frame_003_a1b2c3d4.png / frame_003_s2_a1b2c3d4.png → [ID: …]."""
    m = re.match(
        r"frame_(\d{3})_(?:s2_)?([a-f0-9]{8})\.png$",
        path.name,
        re.I,
    )
    if not m:
        return None
    frame = int(m.group(1))
    hex8 = m.group(2).lower()
    prefix = build_gen_id_prefix(project_id, frame, hex8)
    if "_s2_" in path.name.lower():
        prefix = prefix + "-S2"
    return prefix


def collect_stub_prefixes(project: Project) -> list[tuple[int, int, str, Path]]:
    """Недокачанные stub-файлы в scenes → кандидаты на history download."""
    scenes = project.data_dir / "scenes"
    if not scenes.is_dir():
        return []
    out: list[tuple[int, int, str, Path]] = []
    for p in scenes.glob("frame_*.png"):
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size >= _READY_BYTES:
            continue
        prefix = rebuild_prefix_from_filename(project.id, p)
        if not prefix:
            continue
        m = re.match(r"frame_(\d{3})_", p.name, re.I)
        if not m:
            continue
        frame = int(m.group(1))
        shot = 2 if "_s2_" in p.name.lower() else 1
        out.append((frame, shot, prefix, p))
    return out
