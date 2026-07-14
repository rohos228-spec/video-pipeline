"""Фоновый remount для кнопки «Монтаж» — HTTP не блокируется."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.db import session_scope
from app.models import Project
from app.services.event_bus import publish_project_event
from app.services.montage_board_meta import montage_meta, set_montage_meta
from app.services.remount_video import remount_video

_JOB_KEY = "montage_job"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_montage_job(project: Project) -> dict[str, Any]:
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


async def run_montage_job(project_id: int) -> None:
    try:
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return
            _set_job(
                project,
                {"status": "running", "error": None, "started_at": _utc_now(), "finished_at": None},
            )

        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return
            result = await remount_video(session, project, run_assemble=True)
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
            await publish_project_event(
                project_id,
                event_type="project_updated",
                payload={"montage_board_montage": True, "status": get_montage_job(project).get("status")},
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("montage_job #{} failed", project_id)
        try:
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is not None:
                    _set_job(
                        project,
                        {"status": "error", "error": str(exc), "finished_at": _utc_now()},
                    )
                    await publish_project_event(
                        project_id,
                        event_type="project_updated",
                        payload={"montage_board_montage": True, "status": "error"},
                    )
        except Exception:  # noqa: BLE001
            pass
