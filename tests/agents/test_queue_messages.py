"""Режим реплики пишется на конкретную строку orch_messages."""

from __future__ import annotations

import pytest

from app.orchestrator.contracts import AgentRole, MessageMode
from app.orchestrator.db.migrations import connect


def _user_modes(session_id: str) -> list[str | None]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT mode FROM orch_messages
            WHERE session_id = ? AND role = 'user'
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [row["mode"] for row in rows]


@pytest.mark.asyncio
async def test_identical_texts_keep_their_own_modes(runtime) -> None:
    session = "dup-text"
    await runtime.queue.accept(session, "одно и то же", mode=MessageMode.task)
    await runtime.queue.accept(session, "одно и то же", mode=MessageMode.append)
    await runtime.queue.drain()
    assert _user_modes(session) == [MessageMode.task.value, MessageMode.append.value]


@pytest.mark.asyncio
async def test_classifier_failure_marks_that_row_append(runtime) -> None:
    session = "cls-fail"
    runtime.router.fail_if_called = True
    await runtime.queue.accept(session, "добро пожаловать")
    await runtime.queue.drain()
    assert _user_modes(session) == [MessageMode.append.value]
    events = runtime.bus.replay(session, 0)
    failed = [event for event in events if event.type == "classification"]
    assert len(failed) == 1
    assert failed[0].payload.get("error") == "classifier_failed"
    assert failed[0].payload.get("mode") == MessageMode.append.value


@pytest.mark.asyncio
async def test_append_stream_uses_summarizer_role(runtime) -> None:
    await runtime.queue.accept("sum-role", "добро пожаловать")
    await runtime.queue.drain()
    assert runtime.router.last_call["agent_role"] == AgentRole.summarizer.value
