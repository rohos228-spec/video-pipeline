"""Компактный shared context для агентов scene_design."""

from __future__ import annotations

import json

from app.models import Frame, Project

_GENERAL_PLAN_LIMIT = 800

_STYLE_META_KEYS = ("image_style", "visual_style", "img_style", "style")


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
    frames_payload = [
        {
            "uuid": fr.uuid,
            "number": fr.number,
            "закадр": (fr.voiceover_text or "").strip(),
            "длительность": fr.duration_seconds,
        }
        for fr in frames
    ]
    parts.append(
        "# КАДРЫ (JSON)\n" + json.dumps(frames_payload, ensure_ascii=False, indent=1)
    )
    return "\n\n".join(parts)
