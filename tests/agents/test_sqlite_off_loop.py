"""Медленная запись sqlite не занимает event loop."""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.orchestrator import events as events_mod
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_slow_publish_does_not_block_other_coroutines(
    monkeypatch: pytest.MonkeyPatch,
    token: str,
) -> None:
    del token
    started = threading.Event()
    release = threading.Event()
    real = events_mod._insert_event

    def slow(*args: object, **kwargs: object) -> tuple[int, str]:
        started.set()
        if not release.wait(5):
            raise TimeoutError("slow insert was not released")
        return real(*args, **kwargs)

    monkeypatch.setattr(events_mod, "_insert_event", slow)
    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
    )
    set_runtime(runtime)
    other_ran = asyncio.Event()

    async def sibling() -> None:
        await asyncio.sleep(0)
        other_ran.set()

    try:
        sibling_task = asyncio.create_task(sibling())
        publish_task = asyncio.create_task(runtime.bus.publish("slow-db", "ack", {"text": "x"}))
        assert await asyncio.to_thread(started.wait, 5)
        await asyncio.wait_for(other_ran.wait(), timeout=0.5)
        assert not publish_task.done()
        release.set()
        event = await asyncio.wait_for(publish_task, timeout=2)
        await sibling_task
        assert event.type == "ack"
        assert event.seq >= 1
    finally:
        release.set()
        set_runtime(None)
