"""Компакция orch_events: 14 дней и кап 20 000 на сессию."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.orchestrator.contracts import EVENT_RETENTION_DAYS, SESSION_EVENT_CAP
from app.orchestrator.events import compact_events, persisted_types


def _stamp(days_ago: int) -> str:
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_retention_constants() -> None:
    assert EVENT_RETENTION_DAYS == 14
    assert SESSION_EVENT_CAP == 20_000


@pytest.mark.asyncio
async def test_old_events_drop_except_checkpoint_and_approval(runtime) -> None:
    session = "compact-age"
    old = _stamp(15)
    await runtime.bus.publish(session, "ack", {"n": 1}, created_at=old)
    await runtime.bus.publish(session, "checkpoint", {"n": 2}, created_at=old)
    await runtime.bus.publish(session, "approval_required", {"n": 3}, created_at=old)
    await runtime.bus.publish(session, "message", {"n": 4}, created_at=_stamp(0))
    stats = compact_events(session)
    assert stats["deleted_age"] == 1
    assert persisted_types(session) == ["checkpoint", "approval_required", "message"]


@pytest.mark.asyncio
async def test_cap_drops_oldest_ordinary_then_protected(runtime) -> None:
    session = "compact-cap"
    for index in range(4):
        await runtime.bus.publish(session, "message", {"n": index})
    for index in range(3):
        await runtime.bus.publish(session, "checkpoint", {"n": index})
    stats = compact_events(session, cap=5)
    assert stats["deleted_cap"] == 2
    types = persisted_types(session)
    assert len(types) == 5
    assert types.count("checkpoint") == 3
    assert types.count("message") == 2

    protected = "compact-hard"
    for index in range(4):
        await runtime.bus.publish(protected, "checkpoint", {"n": index})
    hard = compact_events(protected, cap=2)
    assert hard["deleted_cap"] == 2
    assert persisted_types(protected) == ["checkpoint", "checkpoint"]
