"""Шаг: создание звуков сопровождения по плану (sfx_plan → sfx_gen).

Провайдер: ElevenLabs SFX API (ELEVENLABS_API_KEY) иначе локальный синтез
(wave, офлайн). Каждый файл верифицируется (ffprobe + RMS). Чекпоинты
per-event — повтор шага доделывает только недостающее. SFX_ENABLED=false —
pass-through.
"""

from __future__ import annotations

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    if project.status is not ProjectStatus.generating_sfx:
        return
    from app.services.sfx_gen import generate_sfx_files
    from app.services.sfx_plan import load_sfx_plan
    from app.settings import settings

    logger.info("[#{}] sfx_gen starting", project.id)

    if not settings.sfx_enabled:
        logger.info("[#{}] sfx_gen disabled — pass-through", project.id)
        project.status = ProjectStatus.sfx_ready
        await session.flush()
        await session.commit()
        return

    plan = load_sfx_plan(project)
    if not plan:
        raise RuntimeError("sfx_gen: нет плана звуков — сначала шаг «План звуков»")

    files = await generate_sfx_files(session, project, plan)
    project.status = ProjectStatus.sfx_ready
    await session.flush()
    await session.commit()
    logger.info("[#{}] sfx_gen done: {} файлов", project.id, len(files))
