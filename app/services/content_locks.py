"""Поля, отредактированные в UI — не перезаписывать из xlsx при sync."""

from __future__ import annotations

from app.models import Project


def ui_locked_fields(project: Project) -> set[str]:
    meta = getattr(project, "meta", None) or {}
    raw = meta.get("ui_locked_fields")
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if x}


def is_ui_locked(project: Project, field: str) -> bool:
    return field in ui_locked_fields(project)


def lock_ui_field(project: Project, field: str) -> None:
    meta = dict(getattr(project, "meta", None) or {})
    locked = ui_locked_fields(project)
    locked.add(field)
    meta["ui_locked_fields"] = sorted(locked)
    project.meta = meta
