"""Скрипт миграции существующих проектов в изолированные базы данных `project.db`.

Для каждого проекта в общей базе данных (data/state.db):
1. Создаёт персональную базу данных `data/videos/<slug>/project.db` (или в sub-папке батча).
2. Переносит таблицы проекта (frames, scenes, texts, prompts, entities, artifacts, edges, runs).
3. Создаёт резервную копию `project.db.bak` перед записью.
4. Исходные данные в state.db остаются нетронутыми!
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Project
from app.project_db import migrate_single_project_data


async def migrate_all_projects() -> None:
    logger.info("Starting migration of all projects to project-isolated databases...")

    async with SessionLocal() as session:
        projects = (await session.execute(select(Project))).scalars().all()
        logger.info("Found {} projects in shared database", len(projects))

        success = 0
        failed = 0
        for p in projects:
            try:
                logger.info("--- Migrating project #{} [{}] ---", p.id, p.slug)
                counts = await migrate_single_project_data(session, p, create_backup=True)
                logger.info(
                    "Project #{} [{}] ok (frames={}, scenes={}, artifacts={})",
                    p.id,
                    p.slug,
                    counts.get("frames", 0),
                    counts.get("scenes", 0),
                    counts.get("artifacts", 0),
                )
                success += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to migrate project #{}: {}", p.id, exc)
                failed += 1

    logger.info(
        "Migration completed! Successfully migrated: {}, failed: {}",
        success,
        failed,
    )


if __name__ == "__main__":
    asyncio.run(migrate_all_projects())
