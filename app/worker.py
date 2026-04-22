"""Фоновый воркер: периодически сканирует БД и продвигает проекты по стейтам.

Пока — скелет. Будет наполнен по мере реализации шагов (план, сценарий, кадровка,
генерация картинок, видео, сборка, публикация).
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from app.db import engine, session_scope
from app.models import Base, Project, ProjectStatus
from app.orchestrator.pipeline import advance_project


async def _loop_once() -> None:
    async with session_scope() as s:
        q = select(Project).where(
            Project.status.in_(
                [
                    ProjectStatus.planning,
                    ProjectStatus.plan_ready,
                    ProjectStatus.script_ready,
                    ProjectStatus.frames_ready,
                    ProjectStatus.hero_ready,
                    ProjectStatus.images_ready,
                    ProjectStatus.animation_prompts_ready,
                    ProjectStatus.videos_ready,
                    ProjectStatus.audio_ready,
                    ProjectStatus.assembled,
                ]
            )
        )
        projects = (await s.execute(q)).scalars().all()
        for p in projects:
            try:
                await advance_project(s, p)
            except Exception:  # noqa: BLE001
                logger.exception("advance_project failed for #{}", p.id)


async def main() -> None:
    logger.info("worker starting")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    while True:
        try:
            await _loop_once()
        except Exception:  # noqa: BLE001
            logger.exception("worker loop iteration failed")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
