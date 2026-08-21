"""Шаг: план звуков сопровождения (SFX) по временной шкале.

GPT-агент: таймлайн кадров + сцены/действие → sfx_events (метки: время,
вид, промт, громкость, duck). Валидация границ/плотности, повтор с
фидбеком, чекпоинт + sfx_plan.json. SFX_ENABLED=false — pass-through.
"""

from __future__ import annotations

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    if project.status is not ProjectStatus.sfx_planning:
        return
    from app.services.sfx_plan import plan_sfx_events
    from app.settings import settings

    logger.info("[#{}] sfx_plan starting", project.id)

    if not settings.sfx_enabled:
        logger.info("[#{}] sfx_plan disabled — pass-through", project.id)
        project.status = ProjectStatus.sfx_plan_ready
        await session.flush()
        await session.commit()
        return

    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    if not frames:
        raise RuntimeError("sfx_plan: нет кадров — сначала split")

    events = await plan_sfx_events(session, project, frames)
    project.status = ProjectStatus.sfx_plan_ready
    await session.flush()
    await session.commit()
    logger.info("[#{}] sfx_plan done: {} событий", project.id, len(events))
