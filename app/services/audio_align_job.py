"""Фоновый job «Разбор аудио» (5 методик) — HTTP не блокируется."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.db import session_scope
from app.models import Project
from app.services.audio_align_run import run_audio_align_for_project
from app.services.event_bus import publish_project_event
from app.services.montage_board_job_state import resolve_job_status
from app.services.montage_board_meta import montage_meta, set_montage_meta
from app.services.step_cancel import (
    is_stop_requested,
    register_advance_task,
    unregister_advance_task,
)

_JOB_KEY = "audio_align_job"
_tasks: dict[int, asyncio.Task[None]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sqlite_locked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "database is locked" in msg or "database is busy" in msg


def get_audio_align_job(project: Project) -> dict[str, Any]:
    board = montage_meta(project)
    job = board.get(_JOB_KEY)
    raw = dict(job) if isinstance(job, dict) else {"status": "idle"}
    return resolve_job_status(project.id, raw, live_tasks=_tasks)


def _set_job(project: Project, patch: dict[str, Any]) -> dict[str, Any]:
    board = montage_meta(project)
    job = dict(board.get(_JOB_KEY) or {})
    job.update(patch)
    board[_JOB_KEY] = job
    set_montage_meta(project, board)
    return job


async def _set_job_retry(project_id: int, patch: dict[str, Any]) -> None:
    last: BaseException | None = None
    for attempt in range(1, 8):
        try:
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    return
                _set_job(project, patch)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _is_sqlite_locked(exc) and attempt < 7:
                await asyncio.sleep(min(1.5 * attempt, 8.0))
                continue
            raise
    if last is not None:
        raise last


async def _publish(project_id: int, status: str) -> None:
    await publish_project_event(
        project_id,
        event_type="project_updated",
        payload={"audio_align": True, "status": status},
    )


def spawn_audio_align_job(
    project_id: int,
    *,
    method: str,
    force_asr: bool = False,
    run_assemble: bool = True,
) -> asyncio.Task[None]:
    prev = _tasks.get(project_id)
    if prev is not None and not prev.done():
        return prev
    task = asyncio.create_task(
        _run_job(project_id, method=method, force_asr=force_asr, run_assemble=run_assemble),
        name=f"audio-align-{project_id}",
    )
    _tasks[project_id] = task
    register_advance_task(project_id, task)

    def _done(t: asyncio.Task[None]) -> None:
        _tasks.pop(project_id, None)
        unregister_advance_task(project_id)
        if t.cancelled():
            logger.info("audio_align_job #{} cancelled", project_id)

    task.add_done_callback(_done)
    return task


async def _run_job(
    project_id: int,
    *,
    method: str,
    force_asr: bool,
    run_assemble: bool,
) -> None:
    await _set_job_retry(
        project_id,
        {
            "status": "running",
            "method": method,
            "force_asr": force_asr,
            "run_assemble": run_assemble,
            "started_at": _utc_now(),
            "error": None,
            "result": None,
        },
    )
    await _publish(project_id, "running")

    try:
        if is_stop_requested(project_id):
            await _set_job_retry(
                project_id,
                {"status": "cancelled", "finished_at": _utc_now()},
            )
            await _publish(project_id, "cancelled")
            return

        result = await run_audio_align_for_project(
            project_id,
            method=method,
            force_asr=force_asr,
            run_assemble=run_assemble,
        )
        # R15 записана → success даже если DB frames вторично упёрлись в lock
        ok = (not result.get("error")) and (
            bool(result.get("done")) or int(result.get("r15_written") or 0) > 0
        )
        err = result.get("error") or result.get("db_frames_error")
        await _set_job_retry(
            project_id,
            {
                "status": "done" if ok else "error",
                "finished_at": _utc_now(),
                "error": None if ok else err,
                "result": {
                    k: result.get(k)
                    for k in (
                        "method",
                        "words_source",
                        "words_n",
                        "crumbs",
                        "r15_written",
                        "final_video",
                        "master_s",
                        "db_frames_error",
                        "engine",
                    )
                    if k in result
                },
            },
        )
        await _publish(project_id, "done" if ok else "error")
    except Exception as exc:  # noqa: BLE001
        logger.exception("audio_align_job #{} failed", project_id)
        msg = str(exc)
        if _is_sqlite_locked(exc):
            msg = "database is locked — повтори через пару секунд (Excel/доска держали SQLite)"
        try:
            await _set_job_retry(
                project_id,
                {
                    "status": "error",
                    "finished_at": _utc_now(),
                    "error": msg,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("audio_align_job #{} cannot persist error status", project_id)
        await _publish(project_id, "error")
