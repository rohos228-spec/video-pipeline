"""Плановая компакция: фейковые часы, checkpoint и approval остаются."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.orchestrator.agents.compaction import EventCompaction, compact_interval_seconds
from app.orchestrator.events import persisted_types


def test_compact_hours_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_EVENT_COMPACT_HOURS", raising=False)
    assert compact_interval_seconds() == 6 * 3600
    monkeypatch.setenv("ORCH_EVENT_COMPACT_HOURS", "1")
    assert compact_interval_seconds() == 3600
    monkeypatch.setenv("ORCH_EVENT_COMPACT_HOURS", "nope")
    assert compact_interval_seconds() == 6 * 3600


@pytest.mark.asyncio
async def test_compaction_timer_keeps_checkpoint_and_approval(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_EVENT_COMPACT_HOURS", raising=False)
    session = "compact-job"
    start = datetime(2026, 6, 1, tzinfo=UTC)
    old = (start - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    fresh = start.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    await runtime.bus.publish(session, "message", {"n": 1}, created_at=old)
    await runtime.bus.publish(session, "checkpoint", {"n": 2}, created_at=old)
    await runtime.bus.publish(session, "approval_required", {"n": 3}, created_at=old)
    await runtime.bus.publish(session, "approval_decided", {"n": 4}, created_at=old)
    await runtime.bus.publish(session, "message", {"n": 5}, created_at=fresh)
    clock = {"now": start}
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)
        if len(sleeps) >= 2:
            job._stop.set()

    job = EventCompaction(sleep=fake_sleep, clock=lambda: clock["now"])
    job.start()
    await job._task
    assert sleeps[0] == 6 * 3600
    assert persisted_types(session) == [
        "checkpoint",
        "approval_required",
        "approval_decided",
        "message",
    ]


@pytest.mark.asyncio
async def test_compaction_shutdown_cancels_the_task() -> None:
    started = asyncio.Event()

    async def hang(seconds: float) -> None:
        del seconds
        started.set()
        await asyncio.Event().wait()

    job = EventCompaction(sleep=hang, interval=10)
    job.start()
    await started.wait()
    await job.shutdown()
    assert job._task is None
