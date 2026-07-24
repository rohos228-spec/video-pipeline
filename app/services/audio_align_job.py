"""Фоновый job «Разбор аудио» (5 методик) — HTTP не блокируется."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.db import session_scope
from app.models import Project
from app.services.audio_align_run import run_audio_align
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
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            return
        _set_job(
            project,
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
        await session.commit()
    await _publish(project_id, "running")

    try:
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return
            if is_stop_requested(project_id):
                _set_job(
                    project,
                    {"status": "cancelled", "finished_at": _utc_now()},
                )
                await session.commit()
                await _publish(project_id, "cancelled")
                return

            from app.telegram.noop_bot import get_worker_bot

            result = await run_audio_align(
                session,
                project,
                method=method,
                force_asr=force_asr,
                run_assemble=run_assemble,
                bot=get_worker_bot(None),
            )
            ok = bool(result.get("done")) and not result.get("error")
            _set_job(
                project,
                {
                    "status": "done" if ok else "error",
                    "finished_at": _utc_now(),
                    "error": result.get("error"),
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
                        )
                        if k in result
                    },
                },
            )
            await session.commit()
            await _publish(project_id, "done" if ok else "error")
    except Exception as exc:  # noqa: BLE001
        logger.exception("audio_align_job #{} failed", project_id)
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is not None:
                _set_job(
                    project,
                    {
                        "status": "error",
                        "finished_at": _utc_now(),
                        "error": str(exc),
                    },
                )
                await session.commit()
        await _publish(project_id, "error")
