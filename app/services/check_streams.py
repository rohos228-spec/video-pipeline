"""Параллельные потоки GPT vision-check (0..10).

0 — не звать API (ошибка, если нужны картинки на проверке).
1 — батчи подряд.
2..10 — до N батчей одновременно (каждый батч ≤8 PNG).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.models import Project
from app.settings import settings

META_KEY = "check_streams"
CHECK_STREAMS_MIN = 0
CHECK_STREAMS_MAX = 10

_SEM: asyncio.Semaphore | None = None
_SEM_SIZE: int = -1


def clamp_check_streams(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 2
    return max(CHECK_STREAMS_MIN, min(CHECK_STREAMS_MAX, n))


def default_check_streams() -> int:
    return clamp_check_streams(getattr(settings, "check_max_streams", 2) or 2)


def get_check_streams(project: Project | None) -> int:
    if project is not None:
        meta = project.meta if isinstance(project.meta, dict) else {}
        if META_KEY in meta and meta.get(META_KEY) is not None:
            return clamp_check_streams(meta.get(META_KEY))
    return default_check_streams()


def set_check_streams_meta(project: Project, streams: int) -> int:
    from sqlalchemy.orm.attributes import flag_modified

    n = clamp_check_streams(streams)
    meta = dict(project.meta or {})
    meta[META_KEY] = n
    project.meta = meta
    flag_modified(project, "meta")
    return n


def _semaphore(size: int) -> asyncio.Semaphore:
    global _SEM, _SEM_SIZE
    n = max(1, clamp_check_streams(size))
    if _SEM is None or _SEM_SIZE != n:
        _SEM = asyncio.Semaphore(n)
        _SEM_SIZE = n
    return _SEM


@asynccontextmanager
async def acquire_check_slot(streams: int) -> AsyncIterator[None]:
    async with _semaphore(streams):
        yield
