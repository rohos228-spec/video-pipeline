"""Очередь монтажа на hub: импорт bundle → music_ready+queued → assembling по слотам."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func, select

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.models import Project, ProjectStatus
from app.services.node_step_params import send_to_main_pc_for_project
from app.settings import settings

META_ENQUEUED = "montage_queue_enqueued"
META_ENQUEUED_AT = "montage_queue_at"

_queue_task: asyncio.Task | None = None


def _parse_ts(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


async def maybe_mark_for_fleet_montage(session, project: Project) -> None:
    """После music_ready: отметить для hub, если «отправить на основной ПК» включено."""
    if not settings.fleet_enabled:
        return
    if not send_to_main_pc_for_project(project):
        return

    meta = dict(project.meta or {})
    project.meta = bundle_svc.mark_montage_ready(meta)
    await session.flush()

    role = (settings.fleet_role or "hub").strip().lower()
    if settings.fleet_montage_hub and role == "hub":
        await enqueue_for_montage(session, project, source_node=settings.fleet_node_name or None)
        await process_montage_queue(session)


async def enqueue_for_montage(
    session,
    project: Project,
    *,
    source_node: str | None = None,
) -> bool:
    """Поставить проект в очередь монтажа (status остаётся music_ready)."""
    if project.status in {
        ProjectStatus.assembling,
        ProjectStatus.assembled,
        ProjectStatus.publishing,
        ProjectStatus.published,
    }:
        return False

    meta = dict(project.meta or {})
    if meta.get(META_ENQUEUED) and project.status == ProjectStatus.music_ready:
        return False

    meta[META_ENQUEUED] = True
    meta[META_ENQUEUED_AT] = datetime.now(timezone.utc).isoformat()
    meta["montage_ready"] = True
    if source_node:
        meta["fleet_source_node"] = source_node
    project.meta = meta
    project.status = ProjectStatus.music_ready
    await session.flush()
    logger.info(
        "montage queue: enqueued {} (#{}) from {}",
        project.slug,
        project.id,
        source_node or "local",
    )
    return True


async def count_assembling(session) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Project)
                .where(Project.status == ProjectStatus.assembling)
            )
        ).scalar()
        or 0
    )


async def count_queued(session) -> int:
    rows = (
        await session.execute(
            select(Project).where(Project.status == ProjectStatus.music_ready)
        )
    ).scalars().all()
    return sum(1 for p in rows if (p.meta or {}).get(META_ENQUEUED))


async def queued_projects_ordered(session) -> list[Project]:
    rows = (
        await session.execute(
            select(Project).where(Project.status == ProjectStatus.music_ready)
        )
    ).scalars().all()
    queued = [p for p in rows if (p.meta or {}).get(META_ENQUEUED)]
    queued.sort(key=lambda p: _parse_ts((p.meta or {}).get(META_ENQUEUED_AT)))
    return queued


async def queue_position_for_project(session, project: Project) -> int | None:
    if project.status != ProjectStatus.music_ready:
        return None
    if not (project.meta or {}).get(META_ENQUEUED):
        return None
    for i, row in enumerate(await queued_projects_ordered(session), start=1):
        if row.id == project.id:
            return i
    return None


async def process_montage_queue(session) -> int:
    """Запустить следующий проект из очереди, если есть свободный слот."""
    if not settings.fleet_montage_hub:
        return 0

    max_parallel = max(1, int(settings.fleet_montage_max_parallel))
    assembling = await count_assembling(session)
    slots = max_parallel - assembling
    if slots <= 0:
        return 0

    rows = (
        await session.execute(
            select(Project).where(Project.status == ProjectStatus.music_ready)
        )
    ).scalars().all()
    queued = [p for p in rows if (p.meta or {}).get(META_ENQUEUED)]
    queued.sort(key=lambda p: _parse_ts((p.meta or {}).get(META_ENQUEUED_AT)))

    started = 0
    for project in queued[:slots]:
        meta = dict(project.meta or {})
        meta.pop(META_ENQUEUED, None)
        meta["montage_queue_started_at"] = datetime.now(timezone.utc).isoformat()
        project.meta = meta
        project.status = ProjectStatus.assembling
        await session.flush()
        logger.info("montage queue: assembling {} (#{})", project.slug, project.id)
        started += 1
    return started


async def _montage_queue_loop() -> None:
    while True:
        try:
            if settings.fleet_montage_hub and (settings.fleet_role or "hub").lower() == "hub":
                async with session_scope() as session:
                    await process_montage_queue(session)
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("montage queue loop error: {}", exc)
        await asyncio.sleep(10)


def start_montage_queue_loop() -> None:
    global _queue_task
    if not settings.fleet_montage_hub:
        return
    if (settings.fleet_role or "hub").lower() != "hub":
        return
    if _queue_task and not _queue_task.done():
        return
    _queue_task = asyncio.create_task(_montage_queue_loop())
    logger.info(
        "montage queue loop started (max_parallel={})",
        settings.fleet_montage_max_parallel,
    )
