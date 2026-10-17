"""Режим реплики пишется на конкретную строку orch_messages."""

from __future__ import annotations

import pytest

from app.orchestrator.contracts import MessageMode
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
