"""Фоновая компакция ``orch_events``.

Интервал — ``ORCH_EVENT_COMPACT_HOURS`` (по умолчанию 6). Checkpoint и
approval эта задача не вычищает: это делает ``compact_events``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from app.orchestrator.events import compact_events

_DEFAULT_HOURS = 6.0


def compact_interval_seconds() -> float:
    raw = os.environ.get("ORCH_EVENT_COMPACT_HOURS", "").strip()
    hours = _DEFAULT_HOURS
    if raw:
        try:
            hours = float(raw)
        except ValueError:
            hours = _DEFAULT_HOURS
    if hours <= 0:
        hours = _DEFAULT_HOURS
    return hours * 3600.0


class EventCompaction:
    def __init__(
        self,
        *,
        interval: float | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
        compact: Callable[..., dict[str, int]] | None = None,
    ) -> None:
        self.interval = compact_interval_seconds() if interval is None else float(interval)
        self._sleep = sleep
        self._clock = clock
        self._compact = compact or compact_events
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="orch-event-compact")
        return self._task

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if self._sleep is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            else:
                await self._sleep(self.interval)
            if self._stop.is_set():
                return
            now = self._clock() if self._clock is not None else None
            if now is None:
                self._compact()
            else:
                self._compact(now=now)

    def request_stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    async def shutdown(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def attach_compaction(runtime: Any) -> EventCompaction:
    """Повесить задачу, если цикл уже запущен. Иначе она стартует позже из ``shutdown``-пары."""
    job = EventCompaction()
    runtime.compaction = job
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return job
    job.start()
    return job
