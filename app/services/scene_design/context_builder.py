"""Компактный shared context для агентов scene_design."""

from __future__ import annotations

import json

from app.models import Frame, Project
from app.settings import settings

_GENERAL_PLAN_LIMIT = 800

_STYLE_META_KEYS = ("image_style", "visual_style", "img_style", "style")


def frame_seconds(fr: Frame) -> tuple[float, str]:
    """Хронометраж кадра: (секунды, источник).

    Источник «бд» — строка «время кадра» (``duration_seconds``, заполняется
    вручную в таблице или шагом озвучки). Если в БД пусто — оценка по длине
    закадра кадра (``SUBTITLE_CHARS_PER_SECOND``, по умолчанию 14 сим/сек).
    """
    dur = getattr(fr, "duration_seconds", None)
    if dur is not None and dur > 0:
        return round(float(dur), 1), "бд"
    text = (getattr(fr, "voiceover_text", None) or "").strip()
    cps = max(float(settings.subtitle_chars_per_second), 1.0)
    return round(len(text) / cps, 1), "оценка"


def full_voiceover(project: Project, frames: list[Frame]) -> str:
    """Цельный закадр: script_text проекта или склейка кадров по порядку."""
    vo = (project.script_text or "").strip()
    if vo:
        return vo
    return "\n".join(
        (fr.voiceover_text or "").strip()
        for fr in frames
        if (fr.voiceover_text or "").strip()
    ).strip()


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
    for fr in frames:
        sec, source = frame_seconds(fr)
        total_sec += sec
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
        "время_сек — хронометраж кадра: источник «бд» = строка «время кадра» "
        "в таблице (точное), «оценка» = примерно по длине закадра "
        f"(~{settings.subtitle_chars_per_second:g} сим/сек). "
        f"ИТОГО по всем кадрам: {round(total_sec, 1)} сек.\n"
        + json.dumps(frames_payload, ensure_ascii=False, indent=1)
    )
    return "\n\n".join(parts)
