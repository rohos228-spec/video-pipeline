"""Чат оркестратора: приём сообщения и SSE-поток.

POST ``/api/orchestrator/chat/{session}/message``
GET  ``/api/orchestrator/chat/{session}/stream``  (Last-Event-ID)
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sse_starlette.sse import EventSourceResponse

from app.orchestrator.agents.runtime import get_runtime
from app.orchestrator.auth import require_orch_token
from app.orchestrator.contracts import MessageMode, OrchEvent

router = APIRouter(
    prefix="/orchestrator/chat",
    tags=["orchestrator-chat"],
    dependencies=[Depends(require_orch_token)],
)

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# Как часто перепроверять дисконнект, пока в очереди тишина.
_STREAM_POLL_SEC = 1.0


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    mode: MessageMode | None = None
    project_id: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _drop_client_change_id(cls, data: Any) -> Any:
        """change_id одобрения назначает только сервер. Поле клиента отбрасывается."""
        if isinstance(data, dict) and "change_id" in data:
            data = dict(data)
            data.pop("change_id", None)
        return data


def _check_session(session: str) -> str:
    if not _SESSION_RE.fullmatch(session):
        raise HTTPException(status_code=400, detail="bad session")
    return session


def remember_seq(cursor: int, seq: int) -> tuple[int, bool]:
    """Отдать seq один раз. Курсор — последнее число, не растущее множество."""
    if seq <= cursor:
        return cursor, False
    return seq, True


def _sse(event: OrchEvent) -> dict[str, str]:
    return {
        "event": event.type,
        "id": str(event.seq),
        "data": event.model_dump_json(),
    }


@router.post("/{session}/message")
async def post_message(session: str, body: MessageIn) -> dict[str, object]:
    """Принять реплику. Ack уходит в шину до классификации, ответ HTTP её не ждёт."""
    _check_session(session)
    runtime = get_runtime()
    ack_seq = await runtime.queue.accept(session, body.text, body.mode, project_id=body.project_id)
    return {"ok": True, "session": session, "ack_seq": ack_seq}


@router.get("/{session}/stream")
async def stream_session(session: str, request: Request) -> EventSourceResponse:
    """SSE. ``Last-Event-ID`` — последний увиденный seq, replay строго после него."""
    _check_session(session)
    last_header = request.headers.get("last-event-id")
    try:
        last_seq = int(last_header) if last_header else 0
    except ValueError:
        last_seq = 0

    async def _gen():
        runtime = get_runtime()
        cursor = last_seq
        async with runtime.bus.subscription(session) as queue:
            for event in await runtime.bus.replay_from(session, last_seq):
                cursor, fresh = remember_seq(cursor, event.seq)
                if fresh:
                    yield _sse(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_STREAM_POLL_SEC)
                except TimeoutError:
                    continue
                cursor, fresh = remember_seq(cursor, event.seq)
                if not fresh:
                    continue
                yield _sse(event)

    return EventSourceResponse(_gen(), ping=15)
