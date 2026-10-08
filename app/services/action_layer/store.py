"""Таблицы слоя действий (только новые) и доступ к сессиям мастер-БД.

``action_layer_history`` append-only: на SQLite UPDATE/DELETE блокируются
триггерами, а модуль не предоставляет API изменения записей.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import (JSON, Boolean, DateTime, Float, Index, Integer, String, Text,
                        UniqueConstraint, event)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.services.redact import redact, redact_text

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
    # B02: корреляция. Для уже существующих БД колонки добавляет ensure_action_layer_schema.
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)


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


CALL_STARTED = "started"
CALL_DONE = "done"
CALL_FAILED = "failed"


class ActionCallRecord(Base):
    """D12: журнал записывающих вызовов слоя с Idempotency-Key (повтор не дублирует запись).

    Ключ шага + отпечаток запроса (метод, путь, query, тело, файлы) уникальны. Строка ``started``
    без ``done`` означает, что исход предыдущей попытки неизвестен: повтор запрещён.
    """
    __tablename__ = "action_layer_calls"
    __table_args__ = (UniqueConstraint("idem_key", "fingerprint", name="uq_action_layer_call"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    idem_key: Mapped[str] = mapped_column(String(80), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(8), default="")
    path: Mapped[str] = mapped_column(String(300), default="")
    operator: Mapped[str] = mapped_column(String(120), default="")
    plan_id: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(12), default=CALL_STARTED)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response: Mapped[Any] = mapped_column(JSON, nullable=True)
    response_omitted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionSnapshot(Base):
    """D11: прежнее значение, снятое до правки действием слоя (для отката по журналу).

    ``before``/``after`` - значения в том виде, в каком их читает интерфейс. ``restorable=False``
    означает, что прежнее значение записано для истории, но автоматический откат не гарантирован.
    """
    __tablename__ = "action_layer_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    plan_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_id: Mapped[str] = mapped_column(String(120), default="")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="")
    target: Mapped[dict] = mapped_column(JSON, default=dict)
    before: Mapped[Any] = mapped_column(JSON, nullable=True)
    after: Mapped[Any] = mapped_column(JSON, nullable=True)
    restorable: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(120), default="")
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    restored_by: Mapped[str] = mapped_column(String(120), default="")


_TABLES = [ActionPlan.__table__, ActionPlanStep.__table__, ActionApproval.__table__,
           ActionSpend.__table__, ActionHistory.__table__,
           ActionPlanContext.__table__, ActionMacro.__table__,
           ActionCallRecord.__table__, ActionSnapshot.__table__]

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


_HISTORY_EXTRA_COLUMNS = (("project_id", "INTEGER"), ("execution_id", "TEXT"), ("request_id", "TEXT"))


def _ensure_history_columns(sync_conn) -> None:
    """Безопасный ALTER ADD COLUMN: только недостающие колонки, повторный вызов ничего не делает."""
    from sqlalchemy import inspect, text
    insp = inspect(sync_conn)
    if "action_layer_history" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("action_layer_history")}
    for name, ddl in _HISTORY_EXTRA_COLUMNS:
        if name not in have:
            sync_conn.execute(text(f"ALTER TABLE action_layer_history ADD COLUMN {name} {ddl}"))


async def ensure_action_layer_schema(conn) -> None:
    """Идемпотентно создаёт таблицы слоя (вызывается из стартового пути)."""
    await conn.run_sync(lambda sync_conn: Base.metadata.create_all(
        sync_conn, tables=_TABLES, checkfirst=True))
    await conn.run_sync(_ensure_history_columns)
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
                   cost_recorded: float | None = None, project_id: int | None = None,
                   execution_id: str | None = None,
                   request_id: str | None = None) -> ActionHistory:
    """Добавляет запись в журнал в транзакции вызывающего (commit — его дело).

    Текст, параметры и ошибка проходят redact (A16) до записи.
    """
    row = ActionHistory(
        event=event, plan_id=plan_id, step_index=step_index, actor=actor[:120],
        request_text=redact_text(request_text)[:2000], action_id=action_id,
        params=redact(params) if params is not None else None,
        outcome=outcome, error=redact_text(error)[:2000], cost_estimate_low=cost_estimate[0],
        cost_estimate_high=cost_estimate[1], cost_recorded=cost_recorded,
        project_id=project_id, execution_id=execution_id, request_id=request_id)
    session.add(row)
    return row


# --- A16: redact на границе записи ---------------------------------------------
# Любая запись в таблицы слоя (планы, шаги, история, макросы, контекст) проходит
# через redact независимо от того, кто её создал.

_REDACTED_FIELDS: dict[type, tuple[str, ...]] = {
    ActionPlan: ("request_text", "plan_json", "error", "stop_reason"),
    ActionPlanStep: ("params", "progress_note", "result", "error"),
    ActionHistory: ("request_text", "params", "error"),
    ActionMacro: ("description_ru", "steps", "params_schema"),
    ActionCallRecord: ("response",),
    ActionSnapshot: ("target", "before", "after", "note"),
}


def _redact_target(_mapper, _connection, target) -> None:
    for field in _REDACTED_FIELDS[type(target)]:
        old = getattr(target, field, None)
        if old is None:
            continue
        new = redact(old)
        if new != old:
            setattr(target, field, new)


for _model in _REDACTED_FIELDS:
    event.listen(_model, "before_insert", _redact_target)
    if _model is not ActionHistory:  # история append-only: UPDATE заблокирован триггером
        event.listen(_model, "before_update", _redact_target)


# --- D12: идемпотентность записывающих вызовов ---------------------------------------------

_MAX_STORED_RESPONSE = 256 * 1024


def call_fingerprint(method: str, path: str, *, params: Any = None, body: Any = None,
                     files: Any = None) -> str:
    """Отпечаток запроса: тот же ключ с другим запросом - это другой вызов, а не повтор."""
    import hashlib
    import json

    raw = json.dumps({"m": method.upper(), "p": path, "q": params, "b": body, "f": files},
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def claim_call(key: str, fingerprint: str, *, method: str, path: str, operator: str,
                     plan_id: str = "") -> tuple[str, "ActionCallRecord | None"]:
    """Занимает (key, отпечаток). Возвращает ('new', None), ('done', запись) или ('unknown', запись).

    'unknown': предыдущая попытка стартовала и не завершилась записью исхода - нельзя ни повторять,
    ни считать выполненной. Запись со статусом failed (сервер ответил ошибкой) снова берётся в работу.
    """
    from sqlalchemy import select, update
    from sqlalchemy.exc import IntegrityError

    async with sessions()() as s:
        row = ActionCallRecord(idem_key=key[:80], fingerprint=fingerprint, method=method.upper(),
                               path=path[:300], operator=operator[:120], plan_id=plan_id or "",
                               status=CALL_STARTED)
        s.add(row)
        try:
            await s.commit()
            return "new", None
        except IntegrityError:
            await s.rollback()
        existing = await s.scalar(select(ActionCallRecord).where(
            ActionCallRecord.idem_key == key[:80], ActionCallRecord.fingerprint == fingerprint))
        if existing is None:
            return "unknown", None
        if existing.status == CALL_DONE:
            return "done", existing
        if existing.status == CALL_FAILED:
            res = await s.execute(update(ActionCallRecord).where(
                ActionCallRecord.id == existing.id,
                ActionCallRecord.status == CALL_FAILED).values(
                status=CALL_STARTED, finished_at=None, http_status=None))
            await s.commit()
            if res.rowcount == 1:
                return "new", None
        return "unknown", existing


async def reclaim_call(call_id: int) -> bool:
    """Идемпотентный шаг повторяется после обрыва: запись 'started' берётся в работу заново."""
    from sqlalchemy import update

    async with sessions()() as s:
        res = await s.execute(update(ActionCallRecord).where(
            ActionCallRecord.id == call_id,
            ActionCallRecord.status == CALL_STARTED).values(created_at=datetime.utcnow()))
        await s.commit()
        return res.rowcount == 1


async def finish_call(key: str, fingerprint: str, *, ok: bool, http_status: int | None,
                      response: Any = None) -> None:
    """Фиксирует исход: done (с ответом для повтора) или failed (повтор разрешён)."""
    import json

    from sqlalchemy import update

    omitted = False
    stored = response if ok else None
    if ok:
        try:
            if len(json.dumps(response, ensure_ascii=False, default=str)) > _MAX_STORED_RESPONSE:
                stored, omitted = None, True
        except (TypeError, ValueError):
            stored, omitted = None, True
    async with sessions()() as s:
        await s.execute(update(ActionCallRecord).where(
            ActionCallRecord.idem_key == key[:80],
            ActionCallRecord.fingerprint == fingerprint).values(
            status=CALL_DONE if ok else CALL_FAILED, http_status=http_status,
            response=stored, response_omitted=omitted, finished_at=datetime.utcnow()))
        await s.commit()


# --- D11: снимки прежних значений -----------------------------------------------------------

async def save_snapshot(*, plan_id: str, step_index: int | None, action_id: str,
                        project_id: int | None, kind: str, target: dict, before: Any,
                        after: Any, restorable: bool, note: str = "", actor: str = "") -> int:
    # A16: на границе записи значения проходят redact. Если он изменил прежнее значение (в тексте
    # нашлось похожее на секрет), восстановить его точно нельзя: откат отключается, запись остаётся.
    if restorable and (redact(before) != before or redact(after) != after):
        restorable = False
        note = (note + " " if note else "") + (
            "значение содержало похожее на секрет и сохранено со скрытием: автоматический "
            "откат отключён")
    async with sessions()() as s:
        row = ActionSnapshot(plan_id=plan_id or "", step_index=step_index, action_id=action_id,
                             project_id=project_id, kind=kind, target=target, before=before,
                             after=after, restorable=restorable, note=note[:1000],
                             actor=actor[:120])
        s.add(row)
        await s.flush()
        append_history(s, event="snapshot_saved", plan_id=plan_id or "", step_index=step_index,
                       actor=actor, action_id=action_id, project_id=project_id,
                       params={"snapshot_id": row.id, "kind": kind, "restorable": restorable},
                       outcome="saved")
        await s.commit()
        return int(row.id)


async def get_snapshot(snapshot_id: int) -> "ActionSnapshot | None":
    async with sessions()() as s:
        return await s.get(ActionSnapshot, snapshot_id)


async def list_snapshots(project_id: int | None = None, *, limit: int = 50) -> list["ActionSnapshot"]:
    from sqlalchemy import select

    q = select(ActionSnapshot).order_by(ActionSnapshot.id.desc()).limit(max(1, min(limit, 200)))
    if project_id is not None:
        q = q.where(ActionSnapshot.project_id == project_id)
    async with sessions()() as s:
        return list((await s.scalars(q)).all())


async def mark_snapshot_restored(snapshot_id: int, actor: str, *, plan_id: str = "") -> bool:
    """Помечает снимок откатанным один раз (атомарно); False, если уже откатан."""
    from sqlalchemy import update

    async with sessions()() as s:
        res = await s.execute(update(ActionSnapshot).where(
            ActionSnapshot.id == snapshot_id, ActionSnapshot.restored_at.is_(None)).values(
            restored_at=datetime.utcnow(), restored_by=actor[:120]))
        if res.rowcount == 1:
            append_history(s, event="snapshot_restored", plan_id=plan_id, actor=actor,
                           params={"snapshot_id": snapshot_id}, outcome="restored")
        await s.commit()
        return res.rowcount == 1
