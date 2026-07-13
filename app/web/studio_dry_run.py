"""Проверка запуска шага из Studio без побочных эффектов (без сброса артефактов и смены status)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.mass_factory import assert_not_factory_template_for_generation
from app.services.project_state import is_running_status
from app.telegram.menu import step_by_code, step_by_running_status

# Шаги с Outsee / ElevenLabs / CDP — dry_run только для диагностики UI, не для реального старта.
FORBIDDEN_DRY_RUN_STEPS = frozenset({"hero", "items", "img", "video", "audio", "music"})


async def validate_project_step_dry_run(
    session: AsyncSession,
    project: Project,
    step_code: str,
) -> dict[str, object]:
    """Те же проверки, что у start_step, без записи в БД."""
    if step_code in FORBIDDEN_DRY_RUN_STEPS:
        raise ValueError(
            f"dry_run недоступен для шага «{step_code}» (боты Outsee/ElevenLabs)"
        )

    step = step_by_code(step_code)
    if step is None:
        raise ValueError(f"unknown step code: {step_code}")
    assert_not_factory_template_for_generation(project)
    if is_running_status(project.status) and project.status is not step.running_status:
        other = step_by_running_status(project.status)
        other_title = other.title if other is not None else project.status.value
        raise ValueError(
            f"сейчас выполняется «{other_title}» ({project.status.value}). "
            "Остановите ⏹ или дождитесь завершения."
        )

    return {
        "ok": True,
        "step_code": step_code,
        "would_status": step.running_status.value,
        "current_status": project.status.value,
    }
