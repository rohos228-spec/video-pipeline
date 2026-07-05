"""Startup safety guard: never continue old pipeline work automatically."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BatchProject, BatchStatus, Project, ProjectStatus
from app.services.project_state import is_running_status
from app.telegram.menu import step_by_running_status


def _rollback_running_status(status: ProjectStatus) -> ProjectStatus:
    step = step_by_running_status(status)
    if step is not None and step.requires is not None:
        return step.requires
    return ProjectStatus.new


async def block_pipeline_autorun_on_startup(session: AsyncSession) -> dict[str, Any]:
    """Rollback/disable any state that would make the worker start work.

    Policy is intentionally strict: after process restart, old pipeline work
    must wait for an explicit user click. This prevents browser automation and
    generation from resuming unexpectedly.
    """
    from app.orchestrator.auto_advance import TRANSITIONS
    from app.services.mass_pause import set_active as set_mass_pause

    now = datetime.utcnow().isoformat(timespec="seconds")
    stats: dict[str, Any] = {
        "running_projects_rolled_back": 0,
        "auto_mode_disabled": 0,
        "batches_paused": 0,
        "mass_pause_enabled": False,
    }

    projects = (await session.execute(select(Project))).scalars().all()
    ready_statuses = set(TRANSITIONS.keys())

    for project in projects:
        meta = dict(project.meta or {})
        changed = False

        if is_running_status(project.status):
            previous = project.status
            rollback_to = _rollback_running_status(previous)
            project.status = rollback_to
            meta["startup_autorun_blocked"] = True
            meta["startup_blocked_at"] = now
            meta["startup_blocked_running_status"] = previous.value
            meta["startup_rollback_to"] = rollback_to.value
            meta.pop("enrich_auto_chain_to", None)
            stats["running_projects_rolled_back"] += 1
            changed = True
            logger.warning(
                "[#{}] STARTUP GUARD: rolled back {} -> {}",
                project.id,
                previous.value,
                rollback_to.value,
            )

        if project.auto_mode and project.status in ready_statuses:
            project.auto_mode = False
            meta["startup_autorun_blocked"] = True
            meta["startup_blocked_at"] = now
            meta["startup_auto_mode_disabled"] = True
            meta["startup_blocked_ready_status"] = project.status.value
            stats["auto_mode_disabled"] += 1
            changed = True
            logger.warning(
                "[#{}] STARTUP GUARD: disabled auto_mode at {}",
                project.id,
                project.status.value,
            )

        if changed:
            project.meta = meta
            project.updated_at = datetime.utcnow()

    batches = (
        (await session.execute(select(BatchProject).where(BatchProject.status == BatchStatus.running)))
        .scalars()
        .all()
    )
    for batch in batches:
        batch.status = BatchStatus.paused
        batch.updated_at = datetime.utcnow()
        stats["batches_paused"] += 1
        logger.warning("[batch #{}] STARTUP GUARD: running -> paused", batch.id)

    if stats["batches_paused"]:
        set_mass_pause(True)
        stats["mass_pause_enabled"] = True

    if any(
        stats[key]
        for key in ("running_projects_rolled_back", "auto_mode_disabled", "batches_paused")
    ):
        await session.flush()

    return stats
