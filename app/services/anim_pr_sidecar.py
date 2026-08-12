"""Фоновый anim_pr рядом с generating_images.

Пишет промт_видео из image_prompt, не меняя Project.status
(чтобы img продолжал лить PNG). При пустой очереди синхронизирует
NodeRun animation_prompts → done (canvas), без animation_prompts_ready.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Frame, NodeRunStatus, Project, ProjectStatus, WorkflowRun


_LOCKS: dict[int, asyncio.Lock] = {}
_TASKS: dict[int, asyncio.Task[Any]] = {}


def _lock_for(project_id: int) -> asyncio.Lock:
    lock = _LOCKS.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[project_id] = lock
    return lock


async def sync_anim_pr_noderun_done(
    session: AsyncSession, project: Project
) -> bool:
    """Пометить NodeRun animation_prompts = done, если очередь пуста.

    Project.status не трогаем — его двигает только официальный шаг /
    auto_advance после data-guard.
    """
    from app.services.event_bus import publish_node_event
    from app.services.node_status_machine import sync_node_done_from_data

    run = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project.id)
            .options(selectinload(WorkflowRun.node_runs))
            .order_by(WorkflowRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        return False

    changed = False
    for nr in run.node_runs:
        if nr.node_type != "animation_prompts":
            continue
        if nr.status in (NodeRunStatus.done, NodeRunStatus.skipped):
            continue
        old = nr.status
        if sync_node_done_from_data(
            nr, project_id=project.id, initiator="sidecar"
        ):
            changed = True
            logger.info(
                "[#{}] anim_pr_sidecar: NodeRun {}/{} {} → done (queue empty)",
                project.id,
                nr.node_type,
                nr.node_key,
                old.value,
            )
            try:
                await publish_node_event(
                    run.id,
                    event_type="node_status_changed",
                    node_key=nr.node_key,
                    payload={
                        "node_type": nr.node_type,
                        "from": old.value,
                        "to": nr.status.value,
                        "project_id": project.id,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "[#{}] anim_pr_sidecar: publish node event failed",
                    project.id,
                    exc_info=True,
                )
    return changed


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
                await sync_anim_pr_noderun_done(session, project)
                await session.commit()
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
            left = len(apg.collect_batch_items(project, list(frames)))
            if left == 0:
                await sync_anim_pr_noderun_done(session, project)
            await session.commit()
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
                if project is not None and pending == 0:
                    if await sync_anim_pr_noderun_done(session, project):
                        await session.commit()

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
