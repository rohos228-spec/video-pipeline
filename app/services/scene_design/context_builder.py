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


def build_shared_context(project: Project, frames: list[Frame]) -> str:
    """ПОЛНЫЙ ЗАКАДР + КАДРЫ + ОБЩИЙ ПЛАН + СТИЛЬ — общий блок всех агентов."""
    parts: list[str] = []
    plan = general_plan_excerpt(project)
    if plan:
        parts.append(f"# ОБЩИЙ ПЛАН (выдержка)\n{plan}")
    style = project_style(project)
    if style:
        parts.append(f"# СТИЛЬ ПРОЕКТА (задан извне, не менять)\n{style}")
    full_vo = full_voiceover(project, frames)
    if not full_vo:
        raise RuntimeError("scene_design: нет закадра — нечего разбирать")
    parts.append(f"# ПОЛНЫЙ ЗАКАДР\n{full_vo}")
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
    return "\n\n".join(parts)
