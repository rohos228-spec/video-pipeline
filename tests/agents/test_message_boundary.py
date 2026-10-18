"""Граница ответа: message_start, тот же message_id на token, message_end."""

from __future__ import annotations

import pytest

from app.orchestrator.contracts import OrchEvent
from app.orchestrator.db.migrations import connect
from app.orchestrator.events import persisted_types


def _assistant_ids(session_id: str) -> list[int]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT id FROM orch_messages
            WHERE session_id = ? AND role = 'assistant'
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [int(row["id"]) for row in rows]


def _assemble_r33(events: list[OrchEvent]) -> str:
    """Старый клиент клеит token без message_id до события message."""
    parts: list[str] = []
    for event in events:
        if event.type == "token" and "message_id" not in event.payload:
            parts.append(str(event.payload.get("text") or ""))
        elif event.type == "message" and parts:
            break
    return "".join(parts)


def _assemble_r34(events: list[OrchEvent]) -> dict[int, str]:
    buffers: dict[int, list[str]] = {}
    for event in events:
        if event.type != "token" or "message_id" not in event.payload:
            continue
        message_id = int(event.payload["message_id"])
        buffers.setdefault(message_id, []).append(str(event.payload.get("text") or ""))
    return {message_id: "".join(parts) for message_id, parts in buffers.items()}


@pytest.mark.asyncio
async def test_message_boundaries_keep_two_replies_apart(runtime) -> None:
    session = "boundary"
    runtime.router.answer = "добро пожаловать"
    await runtime.queue.accept(session, "который час?")
    await runtime.queue.drain()
    runtime.router.answer = "второй ответ"
    await runtime.queue.accept(session, "и ещё?")
    await runtime.queue.drain()

    events = runtime.bus.replay(session, 0)
    starts = [event for event in events if event.type == "message_start"]
    ends = [event for event in events if event.type == "message_end"]
    tokens = [event for event in events if event.type == "token"]
    assert len(starts) == 2
    assert len(ends) == 2
    ids = _assistant_ids(session)
    assert [event.payload["message_id"] for event in starts] == ids
    assert [event.payload["message_id"] for event in ends] == ids
    assert ids[0] != ids[1]
    assert all(event.payload["role"] == "assistant" for event in starts + ends)

    by_id: dict[int, list] = {message_id: [] for message_id in ids}
    for event in tokens:
        by_id[int(event.payload["message_id"])].append(event)
    assert _assemble_r34(events) == {ids[0]: "добро пожаловать", ids[1]: "второй ответ"}
    for message_id, group in by_id.items():
        start = next(event for event in starts if event.payload["message_id"] == message_id)
        end = next(event for event in ends if event.payload["message_id"] == message_id)
        assert start.seq < group[0].seq < group[-1].seq < end.seq

    assert "token" not in persisted_types(session)
    assert "message_start" in persisted_types(session)
    assert "message_end" in persisted_types(session)
    replayed = runtime.bus.replay(session, starts[0].seq - 1)
    assert [event.type for event in replayed if event.type in {"message_start", "message_end"}] == [
        "message_start",
        "message_end",
        "message_start",
        "message_end",
    ]
    assert _assistant_ids(session) == ids

    legacy = [
        OrchEvent(type="token", seq=1, session_id=session, payload={"text": "раз "}),
        OrchEvent(type="token", seq=2, session_id=session, payload={"text": "два"}),
        OrchEvent(type="message", seq=3, session_id=session, payload={"role": "assistant", "text": "раз два"}),
    ]
    assert _assemble_r33(legacy) == "раз два"
