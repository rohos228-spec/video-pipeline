"""Запуск шагов пайплайна без Telegram (из веб-API)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
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
    project.status = step.running_status
    project.updated_at = datetime.utcnow()
    await session.flush()
    return project.status
