"""Запуск шагов пайплайна без Telegram (из веб-API)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from loguru import logger

from app.models import Project, ProjectStatus
from app.services.mass_factory import assert_not_factory_template_for_generation
from app.services.disabled_nodes import is_step_disabled
from app.orchestrator.graph.planner import assert_step_allowed_by_graph
from app.services.reset_step import clear_step_outputs_for_rerun
from app.services.step_cancel import clear_stop
from app.telegram.menu import step_by_code


def list_step_codes() -> list[dict[str, str]]:
    """Краткий каталог шагов для UI."""
    from app.telegram.menu import steps_for

    out: list[dict[str, str]] = []
    for st in steps_for(None):
        out.append(
            {
                "code": st.code,
                "label": st.label,
                "running_status": st.running_status.value,
                "ready_status": st.ready_status.value,
            }
        )
    return out


async def start_step(
    session: AsyncSession,
    project: Project,
    step_code: str,
) -> ProjectStatus:
    """Перевести проект в running-статус шага — воркер подхватит."""
    step = step_by_code(step_code)
    if step is None:
        raise ValueError(f"unknown step code: {step_code}")
    if is_step_disabled(project, step_code):
        label = step_code.replace("_", " ")
        raise ValueError(f"шаг «{label}» отключён в графе — включите ноду или выберите другой шаг")
    assert_not_factory_template_for_generation(project)
    await assert_step_allowed_by_graph(session, project, step_code)
    clear_stop(project.id)
    meta = dict(project.meta or {})
    cleared: list[str] = []
    if meta.pop("user_stop", None) is not None:
        cleared.append("user_stop")
    if meta.pop("mass_lane_user_stop", None) is not None:
        cleared.append("mass_lane_user_stop")
    if cleared:
        project.meta = meta
        logger.info(
            "[#{}] start_step {}: cleared {}",
            project.id,
            step_code,
            ", ".join(cleared),
        )
    try:
        wiped = await clear_step_outputs_for_rerun(session, project, step_code)
        if wiped:
            logger.info(
                "[#{}] start_step {}: очищены выходы шага перед запуском: {}",
                project.id,
                step_code,
                list(wiped.keys()),
            )
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "[#{}] start_step {}: не удалось очистить выходы шага: {}",
            project.id,
            step_code,
            e,
        )
    project.status = step.running_status
    project.updated_at = datetime.utcnow()
    await session.flush()
    return project.status
