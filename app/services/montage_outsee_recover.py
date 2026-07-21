"""Забрать готовые картинки из истории Outsee в кадры монтажа.

Поиск = strategy C ноды (`discover_prompt_ids_strategy_c` /
`_find_card_by_clicking_images`). Скачивание = `download_image_like_generate`
(тот же путь, что generate_image). Отдельные M1–M5 / D0–D5 запрещены.
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
    discover_prompt_ids_strategy_c,
    download_image_like_generate,
)
from app.generation_options import (
    DEFAULTS,
    IMAGE_GENERATORS_BY_ID,
    build_gen_id_prefix,
)
from app.models import Project
from app.services.montage_board_assets import finalize_scene_image
from app.services.montage_board_meta import add_highlight, montage_meta, set_montage_meta
from app.services.outsee_lane import outsee_lane, outsee_lane_busy
from app.services.plan_shot2 import find_shot1_image, find_shot2_image
from app.settings import settings

_READY_BYTES = 200_000
_WAIT_THUMBS_S = 8.0


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


def _dest_path(project: Project, hit: GalleryHit) -> Path:
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    if hit.shot == 2:
        return scenes / f"frame_{hit.frame_number:03d}_s2_{hit.short_uuid}.png"
    return scenes / f"frame_{hit.frame_number:03d}_{hit.short_uuid}.png"


def _hits_from_disk(project: Project) -> list[GalleryHit]:
    """Prefix с диска — как нода знает [ID] после своей генерации."""
    scenes = project.data_dir / "scenes"
    if not scenes.is_dir():
        return []
    out: list[GalleryHit] = []
    for p in scenes.glob("frame_*.png"):
        prefix = rebuild_prefix_from_filename(project.id, p)
        if not prefix:
            continue
        m = re.match(
            r"frame_(\d{3})_(?:s2_)?([a-f0-9]{8})\.png$",
            p.name,
            re.I,
        )
        if not m:
            continue
        frame = int(m.group(1))
        hex8 = m.group(2).lower()
        shot = 2 if "_s2_" in p.name.lower() else 1
        out.append(
            GalleryHit(
                project_id=project.id,
                frame_number=frame,
                shot=shot,
                short_uuid=hex8,
                prompt_id_prefix=prefix,
                img_src="",
            )
        )
    return out


async def _download_hit(
    page,
    project: Project,
    hit: GalleryHit,
    *,
    force_replace: bool = False,
) -> Path | None:
    """Скачать hit: поиск+download как у ноды (download_image_like_generate)."""
    dest = _dest_path(project, hit)
    if not force_replace and _ready_file(dest):
        return dest

    # Lightbox после клик-скана — закрыть до Download (как Esc в strategy C).
    for _ in range(3):
        try:
            has = await page.evaluate(
                """() => !!document.querySelector('[data-content-viewer="true"]')"""
            )
        except Exception:  # noqa: BLE001
            has = False
        if not has:
            break
        try:
            await page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            break
        await page.wait_for_timeout(120)

    try:
        await download_image_like_generate(
            page,
            out_path=dest,
            img_url=hit.img_src or "",
            gen_id=hit.short_uuid,
            prompt_id_prefix=hit.prompt_id_prefix,
            project_id=project.id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "montage recover P{} F{} shot{} download_image_like_generate: {}",
            project.id,
            hit.frame_number,
            hit.shot,
            e,
        )
        return None if not _ready_file(dest) else dest

    if _ready_file(dest):
        logger.info(
            "montage recover P{} F{} shot{} via download_image_like_generate "
            "(search=strategy C / card_click)",
            project.id,
            hit.frame_number,
            hit.shot,
        )
        return dest
    return None


async def recover_montage_images_from_outsee(
    session: AsyncSession,
    project: Project,
    *,
    frame_filter: set[tuple[int, int]] | None = None,
    click_scan: bool = True,
    limit: int = 30,
    force_replace: bool = False,
) -> dict[str, Any]:
    if outsee_lane_busy():
        raise RuntimeError(
            "Outsee занят другой операцией — дождитесь окончания Generate/apply "
            "и нажмите снова"
        )
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
            n = await _wait_gallery_thumbs(
                page,
                min_count=1,
                timeout_s=_WAIT_THUMBS_S,
                project_id=project.id,
            )
            if n < 1:
                return {
                    "ok": False,
                    "saved": [],
                    "saved_count": 0,
                    "skipped": [],
                    "errors": [
                        "В галерее Outsee нет картинок — откройте outsee.io/image "
                        "в Chrome и убедитесь, что история видна"
                    ],
                    "hits_scanned": 0,
                }

            # === ПОИСК = strategy C ноды (не M1–M5) ===
            scan_limit = max(limit, _GALLERY_ID_SCAN_LIMIT)
            if click_scan:
                discovered = await discover_prompt_ids_strategy_c(
                    page,
                    project_id=project.id,
                    limit=scan_limit,
                    frame_filter=frame_filter,
                )
            else:
                discovered = []

            by_key: dict[tuple[int, int], GalleryHit] = {}
            for d in discovered:
                hit = GalleryHit(
                    project_id=project.id,
                    frame_number=int(d["frame_number"]),
                    shot=int(d["shot"]),
                    short_uuid=str(d["short_uuid"]),
                    prompt_id_prefix=str(d["prompt_id_prefix"]),
                    img_src=str(d.get("img_src") or ""),
                )
                by_key[(hit.frame_number, hit.shot)] = hit

            # Prefix с диска — если strategy C не нашла (старая карточка глубже).
            for disk_hit in _hits_from_disk(project):
                key = (disk_hit.frame_number, disk_hit.shot)
                if frame_filter is not None and key not in frame_filter:
                    continue
                if key not in by_key:
                    by_key[key] = disk_hit

            hits = list(by_key.values())
            logger.info(
                "montage outsee recover #{}: {} hits via strategy C "
                "(filter={}, force={})",
                project.id,
                len(hits),
                sorted(frame_filter) if frame_filter else "all-project",
                force_replace,
            )

            chosen: dict[tuple[int, int], GalleryHit] = {}
            for hit in hits:
                key = (hit.frame_number, hit.shot)
                if frame_filter is not None and key not in frame_filter:
                    continue
                if (
                    not force_replace
                    and not _frame_needs_image(project, hit.frame_number, hit.shot)
                ):
                    skipped.append(f"{hit.frame_number}:{hit.shot}=already")
                    continue
                chosen[key] = hit

            if not hits:
                errors.append(
                    f"В галерее Outsee нет карточек с [ID: P{project.id}-F…] "
                    f"(strategy C / card_click). Откройте outsee.io/image — сверху "
                    f"должны быть свежие генерации этого проекта"
                )
            elif frame_filter and not chosen:
                errors.append(
                    f"Не найдены карточки Outsee для кадров "
                    f"{sorted(frame_filter)} (hits={len(hits)}). "
                    f"Откройте историю outsee.io — сверху должны быть свежие генерации "
                    f"с [ID: P{project.id}-F…]"
                )

            board = montage_meta(project)
            for key, hit in chosen.items():
                path = await _download_hit(
                    page,
                    project,
                    hit,
                    force_replace=force_replace,
                )
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
        "search": "strategy_c",
    }


async def recover_before_regen_ops(
    session: AsyncSession,
    project: Project,
    ops: list[dict[str, Any]],
) -> dict[str, Any]:
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
        session,
        project,
        frame_filter=frame_filter,
        click_scan=True,
        force_replace=False,
        limit=30,
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
