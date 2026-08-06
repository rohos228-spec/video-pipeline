"""Глобальные потоки Studio: параллель проектов + defaults Outsee/check."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import runtime_streams as rs
from app.settings import settings

router = APIRouter(prefix="/runtime-streams", tags=["runtime-streams"])


class RuntimeStreamsPatch(BaseModel):
    worker_max_parallel: int | None = Field(None, ge=1, le=4)
    default_outsee_streams: int | None = Field(None, ge=0, le=4)
    default_check_streams: int | None = Field(None, ge=0, le=10)


@router.get("")
async def get_runtime_streams() -> dict[str, Any]:
    cfg = rs.load_runtime_streams()
    busy = 0
    try:
        from app.db import session_scope
        from app.services.gen_queue import gen_queue_busy_count

        async with session_scope() as session:
            busy = await gen_queue_busy_count(session)
    except Exception:  # noqa: BLE001
        busy = 0
    return {
        **cfg,
        "worker_busy": busy,
        "create_max_parallel_outsee": int(
            getattr(settings, "create_max_parallel_outsee", 4) or 4
        ),
        "limits": {
            "worker_max_parallel": [rs.WORKER_MIN, rs.WORKER_MAX],
            "default_outsee_streams": [rs.OUTSEE_MIN, rs.OUTSEE_MAX],
            "default_check_streams": [rs.CHECK_MIN, rs.CHECK_MAX],
        },
    }


@router.patch("")
async def patch_runtime_streams(body: RuntimeStreamsPatch) -> dict[str, Any]:
    rs.save_runtime_streams(body.model_dump(exclude_none=True))
    return await get_runtime_streams()
