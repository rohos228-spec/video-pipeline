"""Аудит-лог AI-сессий: пишем AISession / AIMessage / AIToolCall в БД.

Каждый шаг сессии (LLM-вызов, tool_call, owner-decision) сохраняется.
Дамп через `/debug ai <session_id>` или `scripts.ai_dump`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AIMessage,
    AIMessageRole,
    AISession,
    AISessionMode,
    AISessionStatus,
    AIToolCall,
    AIToolCallStatus,
)


def _json_safe(obj: Any) -> Any:
    """Сделать объект JSON-сериализуемым (для args_json, result_json)."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return repr(obj)
    return str(obj)


async def create_session(
    db: AsyncSession,
    *,
    chat_id: int,
    initial_query: str,
    model: str,
    mode: AISessionMode = AISessionMode.hitl_edit,
    branch: str | None = None,
) -> AISession:
    """Создать новую AISession и записать system+user сообщения."""
    sess = AISession(
        chat_id=chat_id,
        initial_query=initial_query,
        model=model,
        mode=mode,
        branch=branch,
        status=AISessionStatus.active,
    )
    db.add(sess)
    await db.flush()
    logger.info("ai_agent: created session #{} chat={} model={}", sess.id, chat_id, model)
    return sess


async def append_message(
    db: AsyncSession,
    session: AISession,
    *,
    role: AIMessageRole,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> AIMessage:
    """Добавить сообщение в историю сессии."""
    msg = AIMessage(
        session_id=session.id,
        role=role,
        content=content,
        tool_calls_json=_json_safe(tool_calls) if tool_calls else None,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    db.add(msg)
    # обновим счётчики на сессии
    session.total_tokens_in += tokens_in
    session.total_tokens_out += tokens_out
    await db.flush()
    return msg


async def record_tool_call(
    db: AsyncSession,
    session: AISession,
    message: AIMessage | None,
    *,
    openai_call_id: str,
    tool_name: str,
    args: dict,
) -> AIToolCall:
    """Зафиксировать запрос на tool-call (статус pending)."""
    call = AIToolCall(
        session_id=session.id,
        message_id=message.id if message else None,
        openai_call_id=openai_call_id,
        tool_name=tool_name,
        args_json=_json_safe(args),
        status=AIToolCallStatus.pending,
    )
    db.add(call)
    await db.flush()
    return call


async def update_tool_call_status(
    db: AsyncSession,
    call: AIToolCall,
    *,
    status: AIToolCallStatus,
    result: Any = None,
    error: str | None = None,
    owner_clarification: str | None = None,
    hitl_message_id: int | None = None,
) -> None:
    """Обновить статус tool_call (approved/rejected/executed/failed)."""
    call.status = status
    if result is not None:
        call.result_json = _json_safe(result)
    if error:
        call.error_message = error
    if owner_clarification:
        call.owner_clarification = owner_clarification
    if hitl_message_id is not None:
        call.hitl_message_id = hitl_message_id
    now = datetime.utcnow()
    if status in (AIToolCallStatus.approved, AIToolCallStatus.rejected):
        call.decided_at = now
    if status in (AIToolCallStatus.executed, AIToolCallStatus.failed):
        call.executed_at = now
    await db.flush()


async def close_session(
    db: AsyncSession,
    session: AISession,
    *,
    status: AISessionStatus,
    final_answer: str | None = None,
) -> None:
    """Закрыть сессию (completed / cancelled / failed)."""
    session.status = status
    session.finished_at = datetime.utcnow()
    if final_answer:
        session.final_answer = final_answer
    await db.flush()
    logger.info(
        "ai_agent: closed session #{} status={} tokens={}/{} cost={:.2f}₽ steps={}",
        session.id,
        status.value,
        session.total_tokens_in,
        session.total_tokens_out,
        session.cost_rub,
        session.step_count,
    )


def serialize_session_for_dump(session: AISession) -> dict:
    """Полный JSON-дамп сессии (для /debug ai и scripts.ai_dump)."""
    return {
        "id": session.id,
        "chat_id": session.chat_id,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "finished_at": session.finished_at.isoformat() if session.finished_at else None,
        "status": session.status.value,
        "mode": session.mode.value,
        "model": session.model,
        "branch": session.branch,
        "initial_query": session.initial_query,
        "total_tokens_in": session.total_tokens_in,
        "total_tokens_out": session.total_tokens_out,
        "cost_rub": session.cost_rub,
        "step_count": session.step_count,
        "final_answer": session.final_answer,
        "messages": [
            {
                "id": m.id,
                "role": m.role.value,
                "content": m.content,
                "tool_calls": m.tool_calls_json,
                "tool_call_id": m.tool_call_id,
                "tool_name": m.tool_name,
                "tokens": [m.tokens_in, m.tokens_out],
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in (session.messages or [])
        ],
        "tool_calls": [
            {
                "id": tc.id,
                "openai_call_id": tc.openai_call_id,
                "tool": tc.tool_name,
                "args": tc.args_json,
                "status": tc.status.value,
                "result": tc.result_json,
                "hitl_message_id": tc.hitl_message_id,
                "owner_clarification": tc.owner_clarification,
                "error": tc.error_message,
                "requested_at": tc.requested_at.isoformat() if tc.requested_at else None,
                "decided_at": tc.decided_at.isoformat() if tc.decided_at else None,
                "executed_at": tc.executed_at.isoformat() if tc.executed_at else None,
            }
            for tc in (session.tool_calls or [])
        ],
    }


def session_summary_text(session: AISession) -> str:
    """Краткая текстовая сводка сессии для Telegram-сообщения."""
    parts = [
        f"🤖 AI-сессия #{session.id}",
        f"Статус: {session.status.value}",
        f"Модель: {session.model}",
        f"Шагов: {session.step_count}",
        f"Токены: {session.total_tokens_in}+{session.total_tokens_out}",
    ]
    if session.cost_rub:
        parts.append(f"Стоимость: ~{session.cost_rub:.2f}₽")
    if session.branch:
        parts.append(f"Ветка: {session.branch}")
    if session.final_answer:
        snippet = session.final_answer[:200]
        if len(session.final_answer) > 200:
            snippet += "..."
        parts.append(f"\nИтог:\n{snippet}")
    return "\n".join(parts)


# Используется в scripts/ai_dump.py
__all__ = [
    "_json_safe",
    "append_message",
    "close_session",
    "create_session",
    "record_tool_call",
    "serialize_session_for_dump",
    "session_summary_text",
    "update_tool_call_status",
]


def to_json(obj: Any, *, indent: int = 2) -> str:
    """Удобный JSON-сериализатор для дампов."""
    return json.dumps(_json_safe(obj), ensure_ascii=False, indent=indent)
