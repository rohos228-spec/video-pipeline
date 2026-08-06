"""Сессии агента: CRUD + сборка input для Responses API."""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AgentMessage, AgentSession, AgentSessionStatus


async def create_session(*, title: str = "", autonomy: str = "operator") -> AgentSession:
    async with SessionLocal() as s:
        sess = AgentSession(
            uuid=_uuid.uuid4().hex[:12],
            title=title or "Новая сессия агента",
            autonomy=autonomy,
            status=AgentSessionStatus.idle,
            input_items=[],
        )
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
        return sess


async def get_session(session_id: int) -> AgentSession | None:
    async with SessionLocal() as s:
        return await s.get(AgentSession, session_id)


async def get_session_by_uuid(sess_uuid: str) -> AgentSession | None:
    async with SessionLocal() as s:
        return (
            await s.execute(
                select(AgentSession).where(AgentSession.uuid == sess_uuid)
            )
        ).scalars().first()


async def list_sessions(limit: int = 20) -> list[AgentSession]:
    async with SessionLocal() as s:
        return list(
            (
                await s.execute(
                    select(AgentSession)
                    .order_by(AgentSession.updated_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        )


async def append_message(
    session_id: int,
    *,
    role: str,
    content: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    async with SessionLocal() as s:
        s.add(
            AgentMessage(
                session_id=session_id,
                role=role,
                content=content,
                payload=payload or {},
            )
        )
        await s.commit()


async def load_messages(session_id: int, limit: int = 200) -> list[AgentMessage]:
    async with SessionLocal() as s:
        return list(
            (
                await s.execute(
                    select(AgentMessage)
                    .where(AgentMessage.session_id == session_id)
                    .order_by(AgentMessage.id.desc())
                    .limit(limit)
                )
            ).scalars().all()
        )[::-1]


async def save_state(
    session_id: int,
    *,
    status: AgentSessionStatus | None = None,
    input_items: list[dict[str, Any]] | None = None,
    pending_choice: dict[str, Any] | None = ...,
    title: str | None = None,
) -> None:
    async with SessionLocal() as s:
        sess = await s.get(AgentSession, session_id)
        if sess is None:
            return
        if status is not None:
            sess.status = status
        if input_items is not None:
            sess.input_items = input_items
        if pending_choice is not ...:
            sess.pending_choice = pending_choice
        if title is not None:
            sess.title = title
        await s.commit()
