"""Кросс-проектные блокировки шагов (SQLite + параллельные проекты).

Иначе WORKER_MAX_PARALLEL>1: N проектов одновременно в split/img_pr/…
пишут xlsx/DB → `sqlite3.OperationalError: database is locked` + PendingRollback.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

# Шаг, где три параллельных проекта уже ловили database is locked (split frames).
_SPLIT_LOCK = asyncio.Lock()

# Коды шагов, которые нельзя гонять одновременно между проектами.
STEP_LOCK_CODES = frozenset({"split"})


def step_code_from_status(status) -> str | None:
    """ProjectStatus.running → код шага для lock (если задан)."""
    if status is None:
        return None
    if status.name == "splitting":
        return "split"
    return None


@asynccontextmanager
async def acquire_step_lock(code: str | None) -> AsyncIterator[None]:
    """Сериализовать шаг между проектами. None/неизвестный код — без lock."""
    if code == "split":
        async with _SPLIT_LOCK:
            yield
    else:
        yield
