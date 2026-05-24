"""Фоновый воркер пайплайна — один на процесс, стартует из app.main и web lifespan."""

from __future__ import annotations

import asyncio

from loguru import logger

_worker_task: asyncio.Task | None = None


def ensure_pipeline_worker_started(bot) -> asyncio.Task:
    """Поднимает `_run_worker_loop`, если ещё не запущен."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    from app.main import _run_worker_loop

    _worker_task = asyncio.create_task(_run_worker_loop(bot))
    logger.info("pipeline_worker: background worker started")
    return _worker_task


def pipeline_worker_running() -> bool:
    return _worker_task is not None and not _worker_task.done()


def get_pipeline_worker_task() -> asyncio.Task | None:
    return _worker_task
