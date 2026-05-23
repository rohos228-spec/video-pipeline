"""Запуск шагов пайплайна без Telegram (из веб-API)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services.disabled_nodes import is_step_disabled
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
    from app.orchestrator.graph.planner import graph_executor_enabled, load_graph_for_project

    if graph_executor_enabled(project):
        graph = await load_graph_for_project(session, project)
        if not graph.is_step_reachable(project, step_code):
            raise ValueError(
                f"шаг «{step_code.replace('_', ' ')}» недостижим по текущему графу — "
                "проверьте связи и завершённые предшественники"
            )
    project.status = step.running_status
    project.updated_at = datetime.utcnow()
    await session.flush()
    return project.status
