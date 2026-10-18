"""Параллельный publish одной сессии не теряет seq на курсоре SSE."""

from __future__ import annotations

import asyncio

import pytest

from app.orchestrator.events import EventBus
from app.web.routers.orchestrator_chat import remember_seq


@pytest.mark.asyncio
async def test_parallel_publish_reaches_subscriber_without_seq_gaps() -> None:
    bus = EventBus()
    session = "seq-order"
    queue = bus.subscribe(session)
    count = 40
    await asyncio.gather(*(bus.publish(session, "ack", {"i": index}) for index in range(count)))
    seen: list[int] = []
    cursor = 0
    for _ in range(count):
        event = queue.get_nowait()
        cursor, fresh = remember_seq(cursor, event.seq)
        assert fresh
        seen.append(event.seq)
    assert seen == list(range(seen[0], seen[0] + count))
    assert queue.empty()
