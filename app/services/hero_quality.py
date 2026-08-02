"""Проверки качества рефов персонажей (sheet vs cinematic scene).

Harness/оркестратор раньше смотрели только «png есть» + NodeRun=done —
из-за этого c02-сцена в палате проходила как «OK».
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.services.agent_harness import HarnessCheck
from app.services.excel_characters import (
    is_polluted_character_field,
    parse_persons_sheet,
)


def _db_path() -> Path:
    from app import settings as app_settings

    sp = getattr(app_settings.settings, "sqlite_path", None)
    if sp:
        return Path(sp)
    return Path("data") / "state.db"


def png_corners_look_sheet_bg(path: Path) -> bool | None:
    """True если углы почти белые (turnaround sheet). False — скорее сцена."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        im = Image.open(path).convert("RGB")
        w, h = im.size
        if w < 8 or h < 8:
            return None
        pts = [
            im.getpixel((1, 1)),
            im.getpixel((w - 2, 1)),
            im.getpixel((1, h - 2)),
            im.getpixel((w - 2, h - 2)),
            im.getpixel((w // 2, 1)),
            im.getpixel((1, h // 2)),
        ]
        whiteish = 0
        for pix in pts:
            r, g, b = int(pix[0]), int(pix[1]), int(pix[2])
            if r >= 210 and g >= 210 and b >= 210:
                whiteish += 1
        return whiteish >= 4
    except Exception:  # noqa: BLE001
        return None


def _latest_hero_artifacts(project_id: int) -> dict[str, dict[str, Any]]:
    """excel_id → последняя meta artifact hero_reference."""
    db_file = _db_path()
    if not db_file.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        db = sqlite3.connect(str(db_file))
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            SELECT id, path, meta
            FROM artifacts
            WHERE project_id=? AND kind='hero_reference'
            ORDER BY id DESC
            """,
            (project_id,),
        ).fetchall()
        db.close()
    except Exception:  # noqa: BLE001
        return {}
    import json

    for row in rows:
        meta_raw = row["meta"]
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
        except Exception:  # noqa: BLE001
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        eid = str(meta.get("excel_id") or "").strip()
        if not eid or eid in out:
            continue
        out[eid] = {
            "artifact_id": row["id"],
            "path": row["path"],
            "hero_prompt": str(meta.get("hero_prompt") or ""),
            "used_refs": meta.get("used_refs"),
            "meta": meta,
        }
    return out


def collect_hero_quality_checks(
    project_id: int, data_dir: Path
) -> list[HarnessCheck]:
    """Жёсткие проверки: sheet-контракт персонажей."""
    checks: list[HarnessCheck] = []
    xlsx = data_dir / "project.xlsx"
    if not xlsx.is_file():
        return checks
    try:
        persons = parse_persons_sheet(xlsx)
    except Exception as e:  # noqa: BLE001
        # Нет листа «Персонажи» на ранних шагах — не валим весь harness.
        msg = str(e)
        if "Персонажи" in msg or "нет листа" in msg.lower():
            return [
                HarnessCheck(
                    "excel_hero_present",
                    True,
                    "no Персонажи sheet yet",
                )
            ]
        checks.append(HarnessCheck("excel_hero_parse", False, msg[:200]))
        return checks
    if not persons:
        return [
            HarnessCheck("excel_hero_present", True, "no characters sheet rows")
        ]

    arts = _latest_hero_artifacts(project_id)
    bad_sheet: list[str] = []
    bad_prompt: list[str] = []
    polluted: list[str] = []

    for ch in persons:
        if is_polluted_character_field(ch.name) or is_polluted_character_field(ch.look):
            polluted.append(ch.id)
        png = data_dir / "characters" / f"{ch.id}.png"
        art = arts.get(ch.id) or {}
        prompt = str(art.get("hero_prompt") or "")
        low = prompt.lower()
        sheetish = ("turnaround" in low) or ("model sheet" in low) or (
            "character sheet" in low
        )
        # Реф без промта = старый баг: Outsee рисовал cinematic scene.
        if ch.ref_ids and len(prompt.strip()) < 80:
            bad_prompt.append(
                f"{ch.id}: ref->cinematic (hero_prompt empty/short, refs={ch.ref_ids})"
            )
        elif prompt and not sheetish and len(prompt) > 200:
            # длинный промт без sheet — подозрительно
            if any(
                x in low
                for x in (
                    "hospital",
                    "sitting on",
                    "in a room",
                    "cinematic still",
                    "photograph of",
                )
            ):
                bad_prompt.append(f"{ch.id}: prompt looks like scene, not sheet")

        if png.is_file():
            bg = png_corners_look_sheet_bg(png)
            if bg is False:
                bad_sheet.append(
                    f"{ch.id}: PNG углы не белые — похоже на сцену, не turnaround sheet"
                )

    checks.append(
        HarnessCheck(
            "excel_hero_fields_sane",
            not polluted,
            "ok" if not polluted else f"polluted: {', '.join(polluted)}",
        )
    )
    checks.append(
        HarnessCheck(
            "hero_sheet_prompt",
            not bad_prompt,
            "ok" if not bad_prompt else "; ".join(bad_prompt)[:400],
        )
    )
    checks.append(
        HarnessCheck(
            "hero_sheet_png",
            not bad_sheet,
            "ok" if not bad_sheet else "; ".join(bad_sheet)[:400],
        )
    )
    return checks


def hero_quality_diag_lines(project_id: int, data_dir: Path) -> list[str]:
    """Строки для оркестратор-диагностики (живой прогон, не stale telemetry)."""
    checks = collect_hero_quality_checks(project_id, data_dir)
    bad = [c for c in checks if not c.ok]
    if not bad:
        # всё равно явно сказать, что проверяли
        n_ok = len(checks)
        return [
            "ПЕРСОНАЖИ (живая проверка sheet-контракта):",
            f"- ok ({n_ok} checks): файлы/промты похожи на turnaround sheet",
        ]
    lines = ["ПЕРСОНАЖИ — ОШИБКИ КАЧЕСТВА (не статус NodeRun):"]
    for c in bad:
        lines.append(f"- {c.name}: {c.detail}")
    lines.append(
        "- действие: исправь лист «Персонажи» при необходимости и "
        "перегенерируй проблемные cNN (hero regen)"
    )
    return lines
