"""In-process pub/sub event bus.

Объединяет источники событий пайплайна (NodeRun status, HITL pending,
log lines, progress) и подписчиков (WebSocket-клиенты веб-UI, TG-бот,
будущие SSE-стримы).

Без Redis и Celery — всё в одном Python-процессе, как и текущий
`python -m app.main`. Бэкенд каркаса локального веб-сервиса.

Использование:

    bus = get_bus()
    await bus.publish("runs.42", {"type": "node_started", "node_key": "n1"})

    async with bus.subscribe("runs.42") as queue:
        while True:
            evt = await queue.get()
            ...

Каналы:
    runs.<run_id>           — события одного WorkflowRun
    projects.<project_id>   — события одного Project (даже без Workflow)
    hitl.<project_id>       — pending/decided HITL requests
    logs.<run_id>           — лог-строки прогона
    global                  — события «новый проект создан», «список обновился»
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger


class EventBus:
    """Простейший in-memory pub/sub.

    Каждый подписчик получает свою asyncio.Queue. publish — fire-and-forget,
    медленные подписчики не блокируют публикатора (если очередь переполнена —
    самые старые события дропаются, чтобы не утекать память).
    """

    DEFAULT_QUEUE_MAX = 1000

    def __init__(self) -> None:
        # channel -> set[Queue]
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        """Опубликовать событие. Возвращается мгновенно."""
        # Получаем snapshot подписчиков, чтобы не держать лок во время put.
        async with self._lock:
            queues = list(self._subs.get(channel, ()))
            wildcard_queues = list(self._subs.get("*", ()))
        for q in queues + wildcard_queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Сбрасываем самое старое и пытаемся снова.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:  # noqa: BLE001
                    logger.warning("event_bus: drop event on full queue ({})", channel)

    @contextlib.asynccontextmanager
    async def subscribe(
        self, channel: str, *, maxsize: int = DEFAULT_QUEUE_MAX
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Подписаться на канал. Возвращает контекст с очередью.

        Очередь автоматически отсоединяется при выходе из контекста.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        async with self._lock:
            self._subs[channel].add(q)
        try:
            yield q
        finally:
            async with self._lock:
                self._subs[channel].discard(q)
                if not self._subs[channel]:
                    self._subs.pop(channel, None)

    def subscriber_count(self, channel: str) -> int:
        """Сколько живых подписчиков на канале — для отладки."""
        return len(self._subs.get(channel, ()))


_bus_singleton: EventBus | None = None


def get_bus() -> EventBus:
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = EventBus()
    return _bus_singleton


# ────────────────────────────────────────────────────────────────────────────
# Удобные хелперы для типизированных событий пайплайна.
# ────────────────────────────────────────────────────────────────────────────


async def publish_node_event(
    run_id: int,
    *,
    event_type: str,
    node_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Опубликовать событие, относящееся к NodeRun."""
    evt = {
        "type": event_type,
        "run_id": run_id,
        "node_key": node_key,
        **(payload or {}),
    }
    await get_bus().publish(f"runs.{run_id}", evt)
    # Дублируем в глобальный канал — пригодится для дашборда «все запуски».
    await get_bus().publish("global", evt)


async def publish_project_event(
    project_id: int,
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    evt = {
        "type": event_type,
        "project_id": project_id,
        **(payload or {}),
    }
    await get_bus().publish(f"projects.{project_id}", evt)
    await get_bus().publish("global", evt)


async def publish_log_line(run_id: int, line: str, *, level: str = "info") -> None:
    await get_bus().publish(
        f"logs.{run_id}",
        {"type": "log", "run_id": run_id, "level": level, "line": line},
    )


async def publish_hitl_event(
    project_id: int,
    hitl_id: int,
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    evt = {
        "type": event_type,
        "project_id": project_id,
        "hitl_id": hitl_id,
        **(payload or {}),
    }
    await get_bus().publish(f"hitl.{project_id}", evt)
    await get_bus().publish(f"projects.{project_id}", evt)
    await get_bus().publish("global", evt)


async def publish_fleet_transfer_event(
    project_id: int,
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    evt = {
        "type": "fleet_transfer",
        "project_id": project_id,
        **(payload or {}),
    }
    await get_bus().publish(f"projects.{project_id}", evt)
    await get_bus().publish("global", evt)
