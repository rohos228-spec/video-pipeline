"""Shared project list for fleet local/heartbeat pipeline."""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.fleet.montage_queue import META_ENQUEUED, queue_position_for_project
from app.models import Project, ProjectStatus
from app.services.node_step_params import send_to_main_pc_for_project


async def build_pipeline_payload() -> dict:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Project).order_by(Project.updated_at.desc()).limit(50)
            )
        ).scalars().all()
        projects = []
        for project in rows:
            meta = project.meta or {}
            montage_queued = bool(meta.get(META_ENQUEUED)) and project.status == ProjectStatus.music_ready
            queue_pos = (
                await queue_position_for_project(session, project) if montage_queued else None
            )
            projects.append(
                {
                    "id": project.id,
                    "slug": project.slug,
                    "topic": project.topic,
                    "status": project.status.value
                    if hasattr(project.status, "value")
                    else str(project.status),
                    "montage_ready": bool(meta.get("montage_ready"))
                    or project.status in bundle_svc.MONTAGE_READY_STATUSES,
                    "exportable": True,
                    "montage_queued": montage_queued,
                    "montage_queue_position": queue_pos,
                    "send_to_main_pc": send_to_main_pc_for_project(project),
                }
            )
    return {"projects": projects, "count": len(projects)}


def cached_pipeline_from_node(meta: dict | None) -> dict | None:
    if not meta:
        return None
    snapshot = meta.get("pipeline_snapshot")
    if not isinstance(snapshot, list):
        return None
    return {
        "projects": snapshot,
        "count": len(snapshot),
        "cached": True,
        "cached_at": meta.get("pipeline_snapshot_at"),
    }
