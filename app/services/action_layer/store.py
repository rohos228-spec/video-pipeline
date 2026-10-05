"""Таблицы слоя действий (только новые) и доступ к сессиям мастер-БД.

``action_layer_history`` append-only: на SQLite UPDATE/DELETE блокируются
триггерами, а модуль не предоставляет API изменения записей.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import (JSON, Boolean, DateTime, Float, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

PLAN_PROPOSED = "proposed"
PLAN_RUNNING = "running"
PLAN_SUCCEEDED = "succeeded"
PLAN_FAILED = "failed"
PLAN_STOPPED = "stopped"

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
STEP_UNKNOWN = "unknown"
STEP_CANCELLED = "cancelled"


def new_id() -> str:
    return uuid.uuid4().hex


class ActionPlan(Base):
    __tablename__ = "action_layer_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    operator: Mapped[str] = mapped_column(String(120), default="")
    request_text: Mapped[str] = mapped_column(Text, default="")
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    plan_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default=PLAN_PROPOSED)
    risk_max: Mapped[str] = mapped_column(String(16), default="read")
    needs_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_unknown_steps: Mapped[int] = mapped_column(Integer, default=0)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_reason: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionPlanStep(Base):
    __tablename__ = "action_layer_steps"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_action_layer_step_key"),
                      Index("ix_action_layer_steps_plan", "plan_id", "idx"))
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(32))
    idx: Mapped[int] = mapped_column(Integer)
    action_id: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default=STEP_PENDING)
    idempotency_key: Mapped[str] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress_note: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    cost_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionApproval(Base):
    __tablename__ = "action_layer_approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(String(32), index=True)
    plan_hash: Mapped[str] = mapped_column(String(64))
    operator: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionSpend(Base):
    """Журнал расхода кредитов (append-only): резерв +high, сверка actual-high."""
    __tablename__ = "action_layer_spend"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_key: Mapped[str] = mapped_column(String(80), index=True)
    plan_id: Mapped[str] = mapped_column(String(32), default="")
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(12))
    credits: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ActionHistory(Base):
    __tablename__ = "action_layer_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    plan_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(120), default="")
    request_text: Mapped[str] = mapped_column(Text, default="")
    action_id: Mapped[str] = mapped_column(String(120), default="")
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    cost_estimate_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_estimate_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_recorded: Mapped[float | None] = mapped_column(Float, nullable=True)


class ActionPlanContext(Base):
    """Версия контекста, с которой построен план (планировщик или макрос)."""
    __tablename__ = "action_layer_plan_context"
    plan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="planner")
    macro_id: Mapped[str] = mapped_column(String(80), default="")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_scene: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_frame: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ActionMacro(Base):
    """Пользовательский макрос («умная кнопка»). Встроенные живут в коде."""
    __tablename__ = "action_layer_macros"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner: Mapped[str] = mapped_column(String(120), index=True)
    title_ru: Mapped[str] = mapped_column(String(200))
    description_ru: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    params_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_TABLES = [ActionPlan.__table__, ActionPlanStep.__table__, ActionApproval.__table__,
           ActionSpend.__table__, ActionHistory.__table__,
           ActionPlanContext.__table__, ActionMacro.__table__]

_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS action_layer_history_no_update BEFORE UPDATE ON "
    "action_layer_history BEGIN SELECT RAISE(ABORT, 'action history is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS action_layer_history_no_delete BEFORE DELETE ON "
    "action_layer_history BEGIN SELECT RAISE(ABORT, 'action history is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS action_layer_spend_no_update BEFORE UPDATE ON "
    "action_layer_spend BEGIN SELECT RAISE(ABORT, 'spend ledger is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS action_layer_spend_no_delete BEFORE DELETE ON "
    "action_layer_spend BEGIN SELECT RAISE(ABORT, 'spend ledger is append-only'); END",
)


async def ensure_action_layer_schema(conn) -> None:
    """Идемпотентно создаёт таблицы слоя (вызывается из стартового пути)."""
    await conn.run_sync(lambda sync_conn: Base.metadata.create_all(
        sync_conn, tables=_TABLES, checkfirst=True))
    if conn.dialect.name == "sqlite":
        for ddl in _TRIGGERS:
            await conn.exec_driver_sql(ddl)


# --- сессии мастер-БД -------------------------------------------------------

_sessions: Callable[[], Any] | None = None


def configure_sessions(factory: Callable[[], Any] | None) -> None:
    """Подменить фабрику сессий (тесты); None возвращает SessionLocal."""
    global _sessions
    _sessions = factory


def sessions() -> Callable[[], Any]:
    if _sessions is not None:
        return _sessions
    from app.db import SessionLocal
    return SessionLocal


def append_history(session, *, event: str, plan_id: str = "", step_index: int | None = None,
                   actor: str = "", request_text: str = "", action_id: str = "",
                   params: dict | None = None, outcome: str = "", error: str = "",
                   cost_estimate: tuple[float | None, float | None] = (None, None),
                   cost_recorded: float | None = None) -> ActionHistory:
    """Добавляет запись в журнал в транзакции вызывающего (commit — его дело)."""
    row = ActionHistory(
        event=event, plan_id=plan_id, step_index=step_index, actor=actor[:120],
        request_text=request_text[:2000], action_id=action_id, params=params,
        outcome=outcome, error=error[:2000], cost_estimate_low=cost_estimate[0],
        cost_estimate_high=cost_estimate[1], cost_recorded=cost_recorded)
    session.add(row)
    return row
