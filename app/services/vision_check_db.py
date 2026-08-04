"""Снимок БД для vision-check prompt (Entity + Frame + excel_hero fallback)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, Frame, Project

_FRAME_NUM_RE = re.compile(
    r"(?:frame|video_sheet|clip)[_-]?(\d{1,4})(?:[_-]?(?:s2|shot2|02))?",
    re.IGNORECASE,
)


def _attr(attrs: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = attrs.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _excel_hero_chars(project: Project) -> list[dict[str, Any]]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    eh = meta.get("excel_hero")
    if not isinstance(eh, dict):
        return []
    raw = eh.get("characters") or []
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


async def load_character_rows(
    session: AsyncSession, project: Project
) -> list[dict[str, str]]:
    """Entity characters + fallback excel_hero → плоские строки для промта."""
    rows: dict[str, dict[str, str]] = {}
    ents = (
        await session.execute(
            select(Entity)
            .where(Entity.project_id == project.id, Entity.type == "character")
            .order_by(Entity.sort_key, Entity.id)
        )
    ).scalars().all()
    for e in ents:
        code = (e.code or "").strip().lower()
        if not code:
            continue
        attrs = e.attrs if isinstance(e.attrs, dict) else {}
        rows[code] = {
            "id": code,
            "name": (e.name or "").strip() or _attr(attrs, "имя", "name"),
            "look": _attr(attrs, "look", "внешность"),
            "clothes": _attr(attrs, "clothes", "одежда"),
            "char": _attr(attrs, "char", "характер"),
            "rules": _attr(attrs, "rules", "правила"),
        }
    for c in _excel_hero_chars(project):
        cid = str(c.get("id") or "").strip().lower()
        if not cid:
            continue
        if cid not in rows:
            rows[cid] = {
                "id": cid,
                "name": str(c.get("name") or "").strip(),
                "look": str(c.get("look") or "").strip(),
                "clothes": str(c.get("clothes") or "").strip(),
                "char": str(c.get("char") or "").strip(),
                "rules": str(c.get("rules") or "").strip(),
            }
            continue
        # fill empty entity fields from excel_hero
        cur = rows[cid]
        for key, src in (
            ("name", "name"),
            ("look", "look"),
            ("clothes", "clothes"),
            ("char", "char"),
            ("rules", "rules"),
        ):
            if not cur.get(key):
                cur[key] = str(c.get(src) or "").strip()
    return [rows[k] for k in sorted(rows.keys())]


def _persons_from_frame(fr: Frame) -> str:
    attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
    for key in ("персонажи", "characters", "persons"):
        v = attrs.get(key)
        if v is None:
            continue
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if str(x).strip())
        s = str(v).strip()
        if s:
            return s
    return ""


def match_frame_for_image(
    path: Path, frames_by_num: dict[int, Frame]
) -> tuple[Frame | None, int]:
    """Path → (Frame|None, shot 1|2)."""
    name = path.name
    m = _FRAME_NUM_RE.search(name)
    if not m:
        # characters/c01.png — not a scene
        return None, 1
    num = int(m.group(1))
    shot = 2 if re.search(r"(?:s2|shot2|_02)", name, re.IGNORECASE) else 1
    return frames_by_num.get(num), shot


async def build_vision_db_snapshot(
    session: AsyncSession,
    project: Project,
    *,
    image_paths: list[Path] | None = None,
    kind: str | None = None,
) -> str:
    """Текст ``## База (source of truth)`` для checkMode vision."""
    chars = await load_character_rows(session, project)
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    by_num = {fr.number: fr for fr in frames}

    lines: list[str] = ["## База (source of truth)", ""]
    lines.append("### Персонажи (Entity / excel_hero)")
    if not chars:
        lines.append("(нет записей персонажей)")
    else:
        for c in chars:
            lines.append(f"- {c['id']}:")
            if c.get("name"):
                lines.append(f"  имя: {c['name']}")
            if c.get("look"):
                lines.append(f"  внешность: {c['look']}")
            if c.get("clothes"):
                lines.append(f"  одежда: {c['clothes']}")
            if c.get("char"):
                lines.append(f"  характер: {c['char']}")
            if c.get("rules"):
                lines.append(f"  правила: {c['rules']}")
    lines.append("")

    paths = [p for p in (image_paths or []) if p and p.is_file()]
    scene_paths = [p for p in paths if _FRAME_NUM_RE.search(p.name)]
    hero_paths = [
        p
        for p in paths
        if re.match(r"^c\d{1,3}\.(png|jpe?g|webp|gif)$", p.name, re.IGNORECASE)
    ]

    effective_kind = (kind or "").strip().lower()
    if effective_kind == "videos":
        pass
    elif not effective_kind:
        if any(p.name.lower().startswith("video_sheet_") for p in paths):
            effective_kind = "videos"
        elif scene_paths and not hero_paths:
            effective_kind = "scenes"
        elif hero_paths and not scene_paths:
            effective_kind = "hero"
        elif scene_paths:
            effective_kind = "scenes"
        else:
            effective_kind = "hero"

    want_videos = effective_kind == "videos" or any(
        p.name.lower().startswith("video_sheet_") for p in scene_paths
    )

    if effective_kind in ("scenes", "videos") or scene_paths:
        label = "Клипы (video_sheet 3×2)" if want_videos else "Кадры (по приложенным PNG)"
        lines.append(f"### {label}")
        if not scene_paths:
            lines.append("(нет PNG во входе — сверяй все Frame из БД ниже)")
            for fr in frames[:40]:
                if want_videos:
                    ap = (fr.animation_prompt or "").strip()
                    if len(ap) > 400:
                        ap = ap[:400].rstrip() + "…"
                    vo = (fr.voiceover_text or "").strip()
                    if len(vo) > 160:
                        vo = vo[:160].rstrip() + "…"
                    lines.append(
                        f"- frame {fr.number} uuid={fr.uuid} "
                        f"персонажи={_persons_from_frame(fr) or '—'} "
                        f"закадр={vo or '—'} промт_видео={ap or '—'}"
                    )
                else:
                    prompt = (fr.image_prompt or "").strip()
                    if len(prompt) > 400:
                        prompt = prompt[:400].rstrip() + "…"
                    lines.append(
                        f"- frame {fr.number} uuid={fr.uuid} "
                        f"персонажи={_persons_from_frame(fr) or '—'} "
                        f"промт={prompt or '—'}"
                    )
        else:
            for p in scene_paths:
                fr, shot = match_frame_for_image(p, by_num)
                if fr is None:
                    lines.append(f"- file={p.name}: кадр не найден в БД")
                    continue
                attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
                if want_videos or p.name.lower().startswith("video_sheet_"):
                    ap = (fr.animation_prompt or "").strip()
                    if shot == 2:
                        ap = str(attrs.get("animation_prompt_shot2") or ap).strip()
                    if len(ap) > 400:
                        ap = ap[:400].rstrip() + "…"
                    vo = (fr.voiceover_text or "").strip()
                    if len(vo) > 160:
                        vo = vo[:160].rstrip() + "…"
                    lines.append(
                        f"- file={p.name} frame={fr.number} shot={shot} "
                        f"uuid={fr.uuid} персонажи={_persons_from_frame(fr) or '—'} "
                        f"закадр={vo or '—'} промт_видео={ap or '—'} "
                        f"сетка=3x2 cells 1→6 по времени"
                    )
                else:
                    prompt = (fr.image_prompt or "").strip()
                    if shot == 2:
                        prompt = str(attrs.get("image_prompt_shot2") or prompt).strip()
                    if len(prompt) > 400:
                        prompt = prompt[:400].rstrip() + "…"
                    lines.append(
                        f"- file={p.name} frame={fr.number} shot={shot} "
                        f"uuid={fr.uuid} персонажи={_persons_from_frame(fr) or '—'} "
                        f"промт={prompt or '—'}"
                    )
        lines.append("")

    if effective_kind == "hero" or hero_paths:
        lines.append("### Hero PNG во входе")
        if hero_paths:
            for p in hero_paths:
                lines.append(f"- {p.name}")
        else:
            lines.append("(characters/*.png — сверяй с блоком Персонажи выше)")
        lines.append("")

    lines.append(
        "Правило: verdict и regen_* только по этим данным + видимому на картинке."
    )
    return "\n".join(lines).strip()
