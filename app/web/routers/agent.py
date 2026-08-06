"""HTTP API LLM-агента: сессии, ход (ask), стоп, журнал."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.agent import journal, loop, sessions

router = APIRouter(prefix="/agent", tags=["agent"])


class SessionCreate(BaseModel):
    title: str = ""
    autonomy: str = Field("operator", pattern="^(advisor|operator|autopilot)$")


class AskBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


def _session_dict(s) -> dict[str, Any]:
    return {
        "id": s.id,
        "uuid": s.uuid,
        "title": s.title,
        "status": s.status.value,
        "autonomy": s.autonomy,
        "pending_choice": s.pending_choice,
        "created_at": str(s.created_at),
        "updated_at": str(s.updated_at),
    }


@router.post("/sessions")
async def create_session(body: SessionCreate) -> dict[str, Any]:
    s = await sessions.create_session(title=body.title, autonomy=body.autonomy)
    return _session_dict(s)


@router.get("/sessions")
async def list_sessions(limit: int = 20) -> dict[str, Any]:
    rows = await sessions.list_sessions(limit=max(1, min(limit, 100)))
    return {"sessions": [_session_dict(s) for s in rows]}


@router.get("/sessions/{sess_uuid}")
async def get_session(sess_uuid: str, messages_limit: int = 200) -> dict[str, Any]:
    s = await sessions.get_session_by_uuid(sess_uuid)
    if s is None:
        raise HTTPException(404, "session not found")
    msgs = await sessions.load_messages(s.id, limit=max(1, min(messages_limit, 500)))
    return {
        **_session_dict(s),
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "payload": m.payload,
                "created_at": str(m.created_at),
            }
            for m in msgs
        ],
    }


@router.post("/sessions/{sess_uuid}/ask")
async def ask(sess_uuid: str, body: AskBody, background: bool = False) -> dict[str, Any]:
    if background:
        # Фон: ответ придёт через GET /sessions/{uuid} (polling) / WS позже.
        task = asyncio.create_task(loop.run_agent_turn(sess_uuid, body.text))
        task.add_done_callback(
            lambda t: t.exception() if not t.cancelled() else None
        )
        return {"ok": True, "background": True}
    return await loop.run_agent_turn(sess_uuid, body.text)


@router.post("/sessions/{sess_uuid}/stop")
async def stop(sess_uuid: str) -> dict[str, Any]:
    loop.stop_agent(sess_uuid)
    return {"ok": True}


class AnswerBody(BaseModel):
    choice_id: str = Field(..., min_length=1, max_length=100)


@router.post("/sessions/{sess_uuid}/answer")
async def answer(sess_uuid: str, body: AnswerBody) -> dict[str, Any]:
    """Ответ пользователя на интерактивную карточку (кнопки/варианты)."""
    return await loop.resume_with_choice(sess_uuid, body.choice_id)


@router.get("/actions")
async def actions(session_uuid: str | None = None, limit: int = 30) -> dict[str, Any]:
    sid: int | None = None
    if session_uuid:
        s = await sessions.get_session_by_uuid(session_uuid)
        if s is None:
            raise HTTPException(404, "session not found")
        sid = s.id
    return {"actions": await journal.recent_actions(session_id=sid, limit=limit)}
