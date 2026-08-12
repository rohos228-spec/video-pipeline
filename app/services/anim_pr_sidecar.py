"""Фоновый anim_pr рядом с generating_images.

Пишет промт_видео из image_prompt, не меняя Project.status
(чтобы img продолжал лить PNG).
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.models import Frame, Project, ProjectStatus

_LOCKS: dict[int, asyncio.Lock] = {}
_TASKS: dict[int, asyncio.Task[Any]] = {}


def _lock_for(project_id: int) -> asyncio.Lock:
    lock = _LOCKS.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[project_id] = lock
    return lock


async def drain_anim_pr_from_image_prompts(
    project_id: int,
    *,
    max_batches: int | None = 4,
) -> dict[str, int]:
    """Один проход: до max_batches пачек GPT → R48/DB, статус проекта не трогаем."""
    from app.db import SessionLocal
    from app.orchestrator.steps.make_animation_prompts import fill_animation_prompts
    from app.services import animation_prompt_gpt as apg

    async with _lock_for(project_id):
        async with SessionLocal() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return {"batches": 0, "pending": 0}
            frames = (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project_id)
                    .order_by(Frame.number)
                )
            ).scalars().all()
            pending = apg.collect_batch_items(project, list(frames))
            if not pending:
                return {"batches": 0, "pending": 0}
            logger.info(
                "[#{}] anim_pr_sidecar: drain pending={} (status={})",
                project_id,
                len(pending),
                project.status.value,
            )
            stats = await fill_animation_prompts(
                session,
                project,
                finalize_status=False,
                max_batches=max_batches,
            )
            await session.commit()
            left = len(apg.collect_batch_items(project, list(frames)))
            return {"batches": int(stats.get("batches") or 0), "pending": left}


async def _sidecar_loop(project_id: int) -> None:
    from app.db import SessionLocal

    idle_rounds = 0
    try:
        while True:
            try:
                stats = await drain_anim_pr_from_image_prompts(
                    project_id, max_batches=3
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("[#{}] anim_pr_sidecar: drain failed", project_id)
                await asyncio.sleep(20)
                continue

            batches = int(stats.get("batches") or 0)
            pending = int(stats.get("pending") or 0)
            if batches > 0:
                idle_rounds = 0
                await asyncio.sleep(2)
                continue

            async with SessionLocal() as session:
                project = await session.get(Project, project_id)
                status = project.status if project is not None else None

            # Пока img льёт картинки — ждём новые image_prompt/пропуски и крутимся.
            if status is ProjectStatus.generating_images:
                idle_rounds = 0 if pending else idle_rounds + 1
                await asyncio.sleep(12 if pending == 0 else 4)
                if idle_rounds > 90:
                    # ~18 мин пусто при img — выходим, добьёт обычный anim_pr.
                    logger.info(
                        "[#{}] anim_pr_sidecar: idle under generating_images — stop",
                        project_id,
                    )
                    break
                continue

            if pending > 0:
                await asyncio.sleep(4)
                continue

            logger.info(
                "[#{}] anim_pr_sidecar: очередь пуста (status={}) — stop",
                project_id,
                status.value if status is not None else "?",
            )
            break
    finally:
        _TASKS.pop(project_id, None)
        logger.info("[#{}] anim_pr_sidecar: task finished", project_id)


def ensure_anim_pr_sidecar(project_id: int) -> bool:
    """Запустить sidecar-task, если ещё не крутится. True = стартовали сейчас."""
    t = _TASKS.get(project_id)
    if t is not None and not t.done():
        return False
    task = asyncio.create_task(
        _sidecar_loop(project_id),
        name=f"anim_pr_sidecar_{project_id}",
    )
    _TASKS[project_id] = task
    logger.info("[#{}] anim_pr_sidecar: started", project_id)
    return True
