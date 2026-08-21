"""Единый реестр шагов и активных статусов конвейера (Single Source of Truth)."""

from __future__ import annotations

from app.models import ProjectStatus


def running_statuses() -> set[ProjectStatus]:
    """Возвращает полный набор всех running-статусов из реестра StepDef."""
    from app.telegram.menu import STEPS, _STEP_BY_CODE

    statuses: set[ProjectStatus] = {
        step.running_status for step in STEPS if step.running_status
    }
    statuses.update(
        step.running_status
        for step in _STEP_BY_CODE.values()
        if step.running_status
    )
    return statuses


def running_statuses_list() -> list[ProjectStatus]:
    """Список активных running-статусов для использования в SQL in_() выборках воркеров."""
    from app.telegram.menu import status_order

    return sorted(running_statuses(), key=lambda s: status_order(s))