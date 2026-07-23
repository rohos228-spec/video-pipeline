"""Согласование meta apply_job/montage_job с живыми asyncio.Task."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from sqlalchemy import select

from app.db import session_scope
from app.models import Project
from app.services.montage_board_meta import montage_meta, set_montage_meta

_STALE_RUNNING_ERROR = "прервано перезапуском сервера"
_JOB_KEYS = ("apply_job", "montage_job", "recover_outsee_job")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_job_status(
    project_id: int,
    job: dict[str, Any],
    *,
    live_tasks: dict[int, asyncio.Task[Any]],
) -> dict[str, Any]:
    """running в meta без живой задачи → error."""
    out = dict(job)
    if out.get("status") != "running":
        return out
    task = live_tasks.get(project_id)
    if task is not None and not task.done():
        return out
    out["status"] = "error"
    out["error"] = out.get("error") or _STALE_RUNNING_ERROR
    if not out.get("finished_at"):
        out["finished_at"] = _utc_now()
    return out


async def reconcile_stale_montage_jobs_on_startup() -> int:
    """При старте: meta running без живой задачи → error."""
    fixed = 0
    async with session_scope() as session:
        projects = (await session.execute(select(Project))).scalars().all()
        for project in projects:
            board = montage_meta(project)
            changed = False
            for key in _JOB_KEYS:
                raw = board.get(key)
                if not isinstance(raw, dict) or raw.get("status") != "running":
                    continue
                job = dict(raw)
                job["status"] = "error"
                job["error"] = _STALE_RUNNING_ERROR
                job["finished_at"] = _utc_now()
                board[key] = job
                changed = True
                fixed += 1
                logger.warning(
                    "[#{}] montage job {}: running → error (startup reconcile)",
                    project.id,
                    key,
                )
            if changed:
                set_montage_meta(project, board)
    if fixed:
        logger.info("montage jobs startup reconcile: {} stale running cleared", fixed)
    return fixed
