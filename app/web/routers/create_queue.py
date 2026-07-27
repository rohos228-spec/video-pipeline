"""Единая очередь Create: waiting + running (Outsee/Grsai)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.create_jobs import get_job, max_parallel, queue_snapshot
from app.settings import settings

router = APIRouter(prefix="/create", tags=["create-queue"])


@router.get("/queue")
async def create_queue() -> dict[str, Any]:
    """Параллельные задачи и ожидание для Studio Create."""
    snap = queue_snapshot()
    return {
        **snap,
        "outsee_configured": bool((settings.outsee_api_key or "").strip()),
        "grsai_configured": bool((settings.grsai_api_key or "").strip()),
        "max_parallel": max_parallel(),
    }


@router.get("/jobs/{job_id}")
async def create_job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        return {"job_id": job_id, "status": "unknown", "ok": False}
    return job.to_dict()
