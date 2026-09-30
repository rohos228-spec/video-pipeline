"""Шаг: создание звуков сопровождения по плану (sfx_plan → sfx_gen).

Провайдер: ElevenLabs SFX API (ELEVENLABS_API_KEY) иначе локальный синтез
(wave, офлайн). Каждый файл верифицируется (ffprobe + RMS). Чекпоинты
per-event — повтор шага доделывает только недостающее. SFX_ENABLED=false —
pass-through.
"""

from __future__ import annotations

import sys

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus


async def _record_generation_input(session, project, plan):
    observation = sys.modules.get("app.services.observation")
    ctx = observation._attempt.get() if observation is not None else None
    if (ctx is None or ctx.scope.observer_state not in {"active", "degraded"}
            or getattr(ctx, "native_attempt_closed", False)
            or getattr(ctx, "native_node_type", "sfx_gen") != "sfx_gen"):
        return
    try:
        if ctx.scope.project_id != project.id or ctx.session is not session:
            return
        from sqlalchemy import select
        from app.models import Frame
        from app.services.sfx_plan import record_sfx_generation_input
        from app.settings import settings

        with session.no_autoflush:
            frames = list((await session.scalars(select(Frame).where(
                Frame.project_id == ctx.scope.project_id).order_by(Frame.number))).all())
        await record_sfx_generation_input(project, frames,
            source=getattr(ctx, "sfx_selected_plan_source", "unknown"),
            payload=getattr(ctx, "sfx_selected_plan_payload", None), events=plan,
            provider="elevenlabs" if settings.elevenlabs_api_key else "local_synth")
    except Exception as error:
        ctx.observer.mark_gap(ctx.scope.execution_id, error)


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

    await _record_generation_input(session, project, plan)
    files = await generate_sfx_files(session, project, plan)
    meta = dict(project.meta or {})
    meta["sfx_ready"] = True
    meta["sfx_generated"] = True
    project.meta = meta
    project.status = ProjectStatus.sfx_ready
    await session.flush()
    await session.commit()
    logger.info("[#{}] sfx_gen done: {} файлов", project.id, len(files))
