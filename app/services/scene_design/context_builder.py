"""Компактный shared context для агентов scene_design."""

from __future__ import annotations

import json

from app.models import Frame, Project
from app.settings import settings

_GENERAL_PLAN_LIMIT = 800

_STYLE_META_KEYS = ("image_style", "visual_style", "img_style", "style")


def frame_seconds(fr: Frame) -> tuple[float, str]:
    """Хронометраж кадра для scene_design: (секунды, источник).

    SoT — длина закадра / ``SUBTITLE_CHARS_PER_SECOND`` (14 сим/сек).
    ``duration_seconds`` из БД часто занижен (2с при 100+ символах) и
    ломал action-агента («невозможно совместить 14 сим/сек с временем БД»).
    БД показываем только как справку в JSON, если она есть.
    """
    text = (getattr(fr, "voiceover_text", None) or "").strip()
    cps = max(float(settings.subtitle_chars_per_second), 1.0)
    by_vo = round(len(text) / cps, 1) if text else 0.0
    if by_vo > 0:
        return by_vo, "vo_14cps"
    dur = getattr(fr, "duration_seconds", None)
    if dur is not None and dur > 0:
        return round(float(dur), 1), "бд"
    return 0.0, "пусто"


def full_voiceover(project: Project, frames: list[Frame]) -> str:
    """Цельный закадр для scene_design.

    После split SoT — ``voiceover_text`` кадров (склейка по порядку, пустые
    SET-дети пропускаются). ``script_text`` — только fallback, если в кадрах
    ещё нет закадра. Иначе короткий/устаревший script даёт ложные
    «start_words не найдены» при валидации сборки (#59).
    """
    from_frames = "\n".join(
        (fr.voiceover_text or "").strip()
        for fr in frames
        if (fr.voiceover_text or "").strip()
    ).strip()
    if from_frames:
        return from_frames
    return (project.script_text or "").strip()


def project_style(project: Project) -> str:
    """Заданный визуальный стиль генерации (если есть в meta)."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    for key in _STYLE_META_KEYS:
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def general_plan_excerpt(project: Project) -> str:
    plan = (project.general_plan or "").strip()
    if not plan:
        meta = project.meta if isinstance(project.meta, dict) else {}
        plan = str(meta.get("general_plan") or "").strip()
    return plan[:_GENERAL_PLAN_LIMIT]


def compact_characters_slice(payload: dict) -> dict:
    """Короткий индекс cNN для чанков (без длинных био)."""
    items = payload.get("characters") or payload.get("персонажи") or []
    out = []
    for c in items if isinstance(items, list) else []:
        if not isinstance(c, dict):
            continue
        out.append(
            {
                "id": c.get("id") or c.get("id_char"),
                "name": c.get("name") or c.get("имя") or "",
                "role": (c.get("role") or c.get("роль") or "")[:80],
            }
        )
    return {"characters": out}


def compact_world_slice(payload: dict) -> dict:
    """locNN + имя/зоны — без простыней описаний."""
    items = payload.get("locations") or payload.get("локации") or []
    out = []
    for loc in items if isinstance(items, list) else []:
        if not isinstance(loc, dict):
            continue
        zones = loc.get("zones") or loc.get("зоны") or []
        if isinstance(zones, list):
            ztxt = "; ".join(str(z)[:40] for z in zones[:4])
        else:
            ztxt = str(zones)[:120]
        out.append(
            {
                "id": loc.get("id") or loc.get("id_loc"),
                "name": loc.get("name") or loc.get("название") or "",
                "zones": ztxt,
            }
        )
    return {"locations": out}


def compact_style_slice(payload: dict) -> dict:
    """Стиль: только якоря, без длинных arc-текстов."""
    arc = payload.get("style_arc") or payload.get("дуга") or []
    short = []
    for st in (arc if isinstance(arc, list) else [])[:8]:
        if not isinstance(st, dict):
            continue
        short.append(
            {
                "scene_hint": st.get("scene_hint") or st.get("id_scene") or "",
                "tone": (st.get("tone") or st.get("тон") or "")[:60],
            }
        )
    return {
        "style_summary": str(payload.get("report") or payload.get("summary") or "")[:200],
        "style_arc": short,
    }


def compact_slice_payload(title: str, payload: dict) -> dict:
    """Сжать extras для чанка action/camera."""
    t = (title or "").upper()
    if "CHARACTER" in t or "ПЕРСОНАЖ" in t:
        return compact_characters_slice(payload)
    if "WORLD" in t or "МИР" in t or "LOCATION" in t:
        return compact_world_slice(payload)
    if "STYLE" in t or "СТИЛЬ" in t:
        return compact_style_slice(payload)
    return payload


def _skeleton_block_from_checkpoint(project: Project) -> str:
    """Блок # СКЕЛЕТ (JSON) из чекпоинта волны 0 — для волны 1."""
    try:
        from app.services.scene_design import runner as sd_runner
        from app.services.scene_design.agents import SKELETON

        data = sd_runner.load_checkpoint(project, SKELETON)
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(data, dict) or not data.get("scenes"):
        return ""
    slim = {
        "scenes": data.get("scenes") or [],
        "characters_seed": data.get("characters_seed") or [],
        "locations_seed": data.get("locations_seed") or [],
    }
    return (
        "# СКЕЛЕТ (JSON)\n"
        + json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
    )


def build_shared_context(
    project: Project, frames: list[Frame], *, mode: str = "full"
) -> str:
    """ПОЛНЫЙ ЗАКАДР + КАДРЫ + ОБЩИЙ ПЛАН + СТИЛЬ — общий блок всех агентов.

    ``mode="chunk"`` — тощий контекст: короткий план, VO только кадров чанка
    (уже в ``frames``), без простыни general_plan.
    """
    parts: list[str] = []
    slim = mode == "chunk"
    plan = general_plan_excerpt(project)
    if plan:
        parts.append(
            f"# ОБЩИЙ ПЛАН (выдержка)\n{plan[:200 if slim else _GENERAL_PLAN_LIMIT]}"
        )
    style = project_style(project)
    if style:
        parts.append(
            f"# СТИЛЬ ПРОЕКТА (задан извне, не менять)\n"
            f"{style[:120] if slim else style}"
        )
    full_vo = full_voiceover(project, frames)
    if not full_vo:
        raise RuntimeError("scene_design: нет закадра — нечего разбирать")
    vo_title = "# ЗАКАДР ЧАНКА" if slim else "# ПОЛНЫЙ ЗАКАДР"
    parts.append(f"{vo_title}\n{full_vo}")
    frames_payload = []
    total_sec = 0.0
    cps = max(float(settings.subtitle_chars_per_second), 1.0)
    for fr in frames:
        sec, source = frame_seconds(fr)
        total_sec += sec
        # Не тащим duration_seconds из БД — часто мусор (~2с при 100+ сим) и
        # модель возвращает error «не совпадает с 14 сим/сек». SoT = VO/cps.
        frames_payload.append(
            {
                "uuid": fr.uuid,
                "number": fr.number,
                "закадр": (fr.voiceover_text or "").strip(),
                "время_сек": sec,
                "время_источник": source,
            }
        )
    parts.append(
        "# КАДРЫ (JSON)\n"
        f"время_сек — единственный SoT: len(закадр)/{cps:g} сим/сек "
        f"(vo_14cps). Метки БД/ASR игнорировать. ИТОГО: {round(total_sec, 1)} сек.\n"
        + json.dumps(frames_payload, ensure_ascii=False, separators=(",", ":"))
    )
    if not slim:
        sk = _skeleton_block_from_checkpoint(project)
        if sk:
            parts.append(sk)
    return "\n\n".join(parts)
