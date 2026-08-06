"""Журнал действий агента: каждый tool call пишется (память эпизодов + аудит)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AgentAction


async def log_action(
    *,
    session_id: int | None,
    tool: str,
    risk: str,
    args: dict[str, Any],
    status: str,
    result: str,
    project_id: int | None = None,
) -> None:
    async with SessionLocal() as s:
        s.add(
            AgentAction(
                session_id=session_id,
                tool=tool,
                risk=risk,
                args=args,
                status=status,
                result_excerpt=result[:600],
                project_id=project_id,
            )
        )
        await s.commit()


async def recent_actions(
    *, session_id: int | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    async with SessionLocal() as s:
        q = select(AgentAction).order_by(AgentAction.id.desc()).limit(limit)
        if session_id is not None:
            q = (
                select(AgentAction)
                .where(AgentAction.session_id == session_id)
                .order_by(AgentAction.id.desc())
                .limit(limit)
            )
        rows = (await s.execute(q)).scalars().all()
        return [
            {
                "tool": a.tool,
                "risk": a.risk,
                "status": a.status,
                "args": a.args,
                "result_excerpt": a.result_excerpt,
                "project_id": a.project_id,
                "created_at": str(a.created_at),
            }
            for a in rows
        ]
