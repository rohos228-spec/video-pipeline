"""Фоновый remount для кнопки «Монтаж» — HTTP не блокируется."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.db import session_scope
from app.models import Project, ProjectStatus
from app.services.event_bus import publish_project_event
from app.services.montage_board_job_state import resolve_job_status
from app.services.montage_board_meta import montage_meta, set_montage_meta
from app.services.remount_video import remount_video
from app.services.step_cancel import (
    is_stop_requested,
    register_advance_task,
    unregister_advance_task,
)

_JOB_KEY = "montage_job"
_montage_tasks: dict[int, asyncio.Task[None]] = {}


def is_montage_job_live(project_id: int) -> bool:
    """Фоновый remount «Монтаж» держит проект — worker не должен дублировать шаги."""
    task = _montage_tasks.get(project_id)
    return task is not None and not task.done()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_montage_job(project: Project) -> dict[str, Any]:
    board = montage_meta(project)
    job = board.get(_JOB_KEY)
    raw = dict(job) if isinstance(job, dict) else {"status": "idle"}
    return resolve_job_status(project.id, raw, live_tasks=_montage_tasks)


def _set_job(project: Project, patch: dict[str, Any]) -> dict[str, Any]:
    board = montage_meta(project)
    job = dict(board.get(_JOB_KEY) or {})
    job.update(patch)
    board[_JOB_KEY] = job
    set_montage_meta(project, board)
    return job


async def _publish_job(project_id: int, status: str) -> None:
    await publish_project_event(
        project_id,
        event_type="project_updated",
        payload={"montage_board_montage": True, "status": status},
    )


def spawn_montage_job(project_id: int) -> asyncio.Task[None]:
    prev = _montage_tasks.get(project_id)
    if prev is not None and not prev.done():
        return prev
    task = asyncio.create_task(run_montage_job(project_id), name=f"montage-{project_id}")
    _montage_tasks[project_id] = task
    register_advance_task(project_id, task)

    def _done(t: asyncio.Task[None]) -> None:
        _montage_tasks.pop(project_id, None)
        unregister_advance_task(project_id)
        if t.cancelled():
            logger.info("montage_job #{} cancelled", project_id)

    task.add_done_callback(_done)
    return task


async def _cleanup_montage_interrupt(project_id: int) -> None:
    """Сбросить assemble/audio running после отмены remount."""
    from app.services.project_state import compute_actual_status
    from app.services.run_sync import stop_active_running_node

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            return
        if project.status in (ProjectStatus.generating_audio, ProjectStatus.assembling):
            project.status = await compute_actual_status(session, project)
        await stop_active_running_node(session, project)


async def cancel_montage_job(project_id: int) -> bool:
    """⏹ STOP: отменить фоновый remount и сбросить статус в meta."""
    task = _montage_tasks.get(project_id)
    if task is not None and not task.done():
        task.cancel()
    try:
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return False
            board = montage_meta(project)
            job = dict(board.get(_JOB_KEY) or {})
            if job.get("status") != "running":
                return task is not None
            _set_job(
                project,
                {
                    "status": "cancelled",
                    "error": "остановлено пользователем",
                    "finished_at": _utc_now(),
                },
            )
        await _publish_job(project_id, "cancelled")
        await _cleanup_montage_interrupt(project_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("cancel_montage_job #{}: {}", project_id, exc)
        return False


async def _can_skip_asr_on_remount(
    session,  # noqa: ANN001
    project: Project,
    *,
    retry_after_error: bool,
) -> bool:
    """Повтор «Монтаж» после error на assemble — не гонять ASR заново."""
    from sqlalchemy import select

    from app.models import Artifact, ArtifactKind

    if not retry_after_error:
        return False
    if project.status is not ProjectStatus.audio_ready:
        return False
    whisper = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if whisper is None or not whisper.path:
        return False
    meta = whisper.meta if isinstance(whisper.meta, dict) else {}
    words = meta.get("words") or meta.get("word_count")
    if isinstance(words, list):
        return len(words) >= 10
    if isinstance(words, int):
        return words >= 10
    return Path(whisper.path).is_file()


async def run_montage_job(project_id: int) -> None:
    retry_after_error = False
    try:
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return
            from app.services.project_control import clear_user_stop_gate
            from app.services.step_cancel import clear_stop

            clear_user_stop_gate(project)
            clear_stop(project_id)
            board = montage_meta(project)
            prev_job = board.get(_JOB_KEY) if isinstance(board.get(_JOB_KEY), dict) else {}
            retry_after_error = prev_job.get("status") == "error"
            if is_stop_requested(project_id):
                _set_job(
                    project,
                    {
                        "status": "cancelled",
                        "error": "остановлено пользователем",
                        "finished_at": _utc_now(),
                    },
                )
                await _publish_job(project_id, "cancelled")
                return
            _set_job(
                project,
                {"status": "running", "error": None, "started_at": _utc_now(), "finished_at": None},
            )
        await _publish_job(project_id, "running")

        if is_stop_requested(project_id):
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is not None:
                    _set_job(
                        project,
                        {
                            "status": "cancelled",
                            "error": "остановлено пользователем",
                            "finished_at": _utc_now(),
                        },
                    )
            await _publish_job(project_id, "cancelled")
            return

        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return
            if is_stop_requested(project_id):
                _set_job(
                    project,
                    {
                        "status": "cancelled",
                        "error": "остановлено пользователем",
                        "finished_at": _utc_now(),
                    },
                )
                await _publish_job(project_id, "cancelled")
                return
            skip_asr = await _can_skip_asr_on_remount(
                session, project, retry_after_error=retry_after_error
            )
            if skip_asr:
                logger.info(
                    "[#{}] montage_job: audio_ready + whisper в БД — assemble без повторного ASR",
                    project_id,
                )
            result = await remount_video(
                session, project, run_assemble=True, skip_asr=skip_asr
            )
            if is_stop_requested(project_id):
                _set_job(
                    project,
                    {
                        "status": "cancelled",
                        "error": "остановлено пользователем",
                        "finished_at": _utc_now(),
                    },
                )
                await _publish_job(project_id, "cancelled")
                return
            if result.get("error") and not result.get("done"):
                _set_job(
                    project,
                    {
                        "status": "error",
                        "error": str(result.get("error")),
                        "finished_at": _utc_now(),
                        "result": {"done": False},
                    },
                )
                await _publish_job(project_id, "error")
            else:
                _set_job(
                    project,
                    {
                        "status": "done",
                        "error": None,
                        "finished_at": _utc_now(),
                        "result": {
                            "done": bool(result.get("done")),
                            "final_video": result.get("final_video"),
                        },
                    },
                )
                await _publish_job(project_id, "done")
    except asyncio.CancelledError:
        logger.info("montage_job #{} task cancelled", project_id)
        try:
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is not None:
                    _set_job(
                        project,
                        {
                            "status": "cancelled",
                            "error": "остановлено пользователем",
                            "finished_at": _utc_now(),
                        },
                    )
            await _publish_job(project_id, "cancelled")
        except Exception:  # noqa: BLE001
            pass
        await _cleanup_montage_interrupt(project_id)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("montage_job #{} failed", project_id)
        try:
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    return
                if project.status is ProjectStatus.audio_ready:
                    logger.info(
                        "[#{}] montage_job: ASR готов — повтор только assemble после {}",
                        project_id,
                        type(exc).__name__,
                    )
                    result = await remount_video(
                        session, project, run_assemble=True, skip_asr=True
                    )
                    if result.get("done"):
                        _set_job(
                            project,
                            {
                                "status": "done",
                                "error": None,
                                "finished_at": _utc_now(),
                                "result": {
                                    "done": True,
                                    "final_video": result.get("final_video"),
                                    "recovered_after": str(exc)[:500],
                                },
                            },
                        )
                        await _publish_job(project_id, "done")
                        return
                    if result.get("error"):
                        exc = RuntimeError(str(result.get("error")))
                _set_job(
                    project,
                    {"status": "error", "error": str(exc), "finished_at": _utc_now()},
                )
            await _publish_job(project_id, "error")
        except Exception:  # noqa: BLE001
            pass
