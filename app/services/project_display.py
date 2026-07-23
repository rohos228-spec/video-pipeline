"""Отображаемое имя проекта в UI (сайдбар, тосты) — не topic для пайплайна."""

from __future__ import annotations

from app.models import Project


def project_display_name(project: Project) -> str:
    """title → topic (legacy) → slug."""
    title = (getattr(project, "title", None) or "").strip()
    if title:
        return title
    topic = (project.topic or "").strip()
    if topic:
        return topic
    return project.slug
