"""Фоновое «Применить правки» с regen — Outsee может работать минуты."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.db import session_scope
from app.models import Project
from app.services.event_bus import publish_project_event
from app.services.montage_board_apply import apply_montage_board
from app.services.montage_board_meta import montage_meta, set_montage_meta
from app.services.montage_board_montage_job import cancel_montage_job
from app.services.step_cancel import clear_stop

_JOB_KEY = "apply_job"
_apply_tasks: dict[int, asyncio.Task[None]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_apply_job(project: Project) -> dict[str, Any]:
    board = montage_meta(project)
    job = board.get(_JOB_KEY)
    return dict(job) if isinstance(job, dict) else {"status": "idle"}


def _set_job(project: Project, patch: dict[str, Any]) -> dict[str, Any]:
    board = montage_meta(project)
    job = dict(board.get(_JOB_KEY) or {})
    job.update(patch)
    board[_JOB_KEY] = job
    set_montage_meta(project, board)
    return job


async def _publish(project_id: int, status: str, *, extra: dict | None = None) -> None:
    payload: dict[str, Any] = {"montage_board_apply": True, "status": status}
    if extra:
        payload.update(extra)
    await publish_project_event(project_id, event_type="project_updated", payload=payload)


def spawn_apply_job(
    project_id: int,
    *,
    video_trims: dict[str, dict[str, float]] | None,
    pending_ops: list[dict[str, Any]],
) -> asyncio.Task[None]:
    prev = _apply_tasks.get(project_id)
    if prev is not None and not prev.done():
        return prev

    async def _runner() -> None:
        try:
            clear_stop(project_id)
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    return
                _set_job(
                    project,
                    {
                        "status": "running",
                        "error": None,
                        "started_at": _utc_now(),
                        "finished_at": None,
                        "total_ops": len(pending_ops),
                        "done_ops": 0,
                    },
                )
            await _publish(project_id, "running", extra={"total_ops": len(pending_ops)})

            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    return
                result = await apply_montage_board(
                    session,
                    project,
                    video_trims=video_trims,
                    pending_ops=pending_ops,
                )
                status = "done" if result.get("ok") else "error"
                _set_job(
                    project,
                    {
                        "status": status,
                        "error": "; ".join(result.get("errors") or []) or None,
                        "finished_at": _utc_now(),
                        "done_ops": len(pending_ops),
                        "results": result.get("results"),
                    },
                )
            await _publish(
                project_id,
                status,
                extra={"errors": result.get("errors"), "ok": result.get("ok")},
            )
        except asyncio.CancelledError:
            logger.info("apply_job #{} cancelled", project_id)
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
                await _publish(project_id, "cancelled")
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("apply_job #{} failed", project_id)
            try:
                async with session_scope() as session:
                    project = await session.get(Project, project_id)
                    if project is not None:
                        _set_job(
                            project,
                            {"status": "error", "error": str(exc), "finished_at": _utc_now()},
                        )
                await _publish(project_id, "error", extra={"error": str(exc)})
            except Exception:  # noqa: BLE001
                pass

    task = asyncio.create_task(_runner(), name=f"montage-apply-{project_id}")
    _apply_tasks[project_id] = task

    def _done(t: asyncio.Task[None]) -> None:
        _apply_tasks.pop(project_id, None)

    task.add_done_callback(_done)
    return task


async def cancel_apply_job(project_id: int) -> bool:
    task = _apply_tasks.get(project_id)
    if task is not None and not task.done():
        task.cancel()
    try:
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return False
            job = get_apply_job(project)
            if job.get("status") != "running":
                return task is not None and task.cancelled()
            _set_job(
                project,
                {
                    "status": "cancelled",
                    "error": "остановлено пользователем",
                    "finished_at": _utc_now(),
                },
            )
        await _publish(project_id, "cancelled")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("cancel_apply_job #{}: {}", project_id, exc)
        return False


async def cancel_all_montage_jobs(project_id: int) -> None:
    await cancel_apply_job(project_id)
    await cancel_montage_job(project_id)
