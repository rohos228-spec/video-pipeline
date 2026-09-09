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


async def _reset_matching_noderuns_after_rollback(
    session: AsyncSession,
    project: Project,
    previous_running: ProjectStatus,
) -> int:
    """Project rolled back from running → reset matching NodeRun to pending."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import NodeRunStatus, WorkflowRun
    from app.orchestrator.node_registry import RUNNING_TO_NODE_TYPE
    from app.services.node_status_machine import reset_node_to_pending

    want = RUNNING_TO_NODE_TYPE.get(previous_running)
    if not want:
        return 0
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
        return 0
    n = 0
    for nr in run.node_runs:
        if nr.node_type != want:
            continue
        if nr.status not in (NodeRunStatus.running, NodeRunStatus.queued):
            continue
        if reset_node_to_pending(
            nr, project_id=project.id, initiator="auto_unstick"
        ):
            n += 1
            logger.info(
                "[#{}] STARTUP GUARD: NodeRun {}/{} → pending (parity with Project)",
                project.id,
                nr.node_type,
                nr.node_key,
            )
    return n


async def _guard_one_project(
    session: AsyncSession,
    project: Project,
    *,
    now: str,
    stats: dict[str, Any],
    ready_statuses: set[ProjectStatus],
) -> None:
    from app.services.project_control import arm_auto_await_manual_start

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
        meta.pop("startup_auto_mode_disabled", None)
        project.meta = meta
        if arm_auto_await_manual_start(project):
            stats["auto_await_manual_armed"] += 1
        stats["running_projects_rolled_back"] += 1
        changed = True
        try:
            await _reset_matching_noderuns_after_rollback(
                session, project, previous
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "[#{}] STARTUP GUARD: NodeRun reset after rollback failed",
                project.id,
                exc_info=True,
            )
        logger.warning(
            "[#{}] STARTUP GUARD: rolled back {} -> {} (auto_mode={} сохранён)",
            project.id,
            previous.value,
            rollback_to.value,
            project.auto_mode,
        )

    elif project.auto_mode and project.status in ready_statuses:
        if project.status in (
            ProjectStatus.assembled,
            ProjectStatus.publishing,
            ProjectStatus.published,
        ):
            return
        meta["startup_autorun_blocked"] = True
        meta["startup_blocked_at"] = now
        meta["startup_blocked_ready_status"] = project.status.value
        meta.pop("startup_auto_mode_disabled", None)
        project.meta = meta
        if arm_auto_await_manual_start(project):
            stats["auto_await_manual_armed"] += 1
        changed = True
        logger.info(
            "[#{}] STARTUP GUARD: auto_mode сохранён at {} — ждём ▶",
            project.id,
            project.status.value,
        )

    if changed:
        project.meta = dict(project.meta or {})
        project.updated_at = datetime.utcnow()


async def block_pipeline_autorun_on_startup(session: AsyncSession) -> dict[str, Any]:
    """Rollback running work on restart — but keep user's auto_mode preference.

    After process restart, in-flight steps roll back to *_ready and wait for
    explicit ▶. ``auto_mode`` is NOT cleared: user turned it on intentionally;
    ``auto_await_manual_start`` blocks autostart until ▶ (see project_control).
    """
    from app.orchestrator.auto_advance import TRANSITIONS
    from app.services.mass_pause import set_active as set_mass_pause
    from app.services.project_control import arm_auto_await_manual_start

    now = datetime.utcnow().isoformat(timespec="seconds")
    stats: dict[str, Any] = {
        "running_projects_rolled_back": 0,
        "auto_mode_disabled": 0,  # legacy counter — always 0 (auto_mode preserved)
        "auto_await_manual_armed": 0,
        "batches_paused": 0,
        "mass_pause_enabled": False,
    }

    projects = (await session.execute(select(Project))).scalars().all()
    ready_statuses = set(TRANSITIONS.keys())

    for project in projects:
        isolated_ok = False
        try:
            from app.project_db import (
                get_project_data_dir,
                project_db_session_scope,
                push_runtime_to_master,
                resolve_project_db_path,
            )

            dir_path = await get_project_data_dir(int(project.id))
            if dir_path is not None and resolve_project_db_path(dir_path).exists():
                async with project_db_session_scope(int(project.id)) as ps:
                    pp = await ps.get(Project, int(project.id))
                    if pp is not None:
                        await _guard_one_project(
                            ps,
                            pp,
                            now=now,
                            stats=stats,
                            ready_statuses=ready_statuses,
                        )
                        await push_runtime_to_master(ps, pp)
                        isolated_ok = True
        except Exception:  # noqa: BLE001
            logger.warning(
                "[#{}] STARTUP GUARD: isolated project.db rollback failed",
                project.id,
                exc_info=True,
            )
        if not isolated_ok:
            await _guard_one_project(
                session,
                project,
                now=now,
                stats=stats,
                ready_statuses=ready_statuses,
            )

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
        for key in (
            "running_projects_rolled_back",
            "auto_await_manual_armed",
            "batches_paused",
        )
    ):
        await session.flush()

    return stats
