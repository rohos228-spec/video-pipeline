"""Operator-level emergency stop for the whole supervisory layer.

One persisted global flag (master DB, survives restart). While it is ON:

* ``ActionSupervisor.charge_request`` refuses before the atomic reservation, so
  no worker, decision, media or retrieval request of a managed execution is
  charged or sent;
* ``worker_entry_admission`` refuses a new native tick of a managed project;
* ``recover_node`` refuses before any ticket is reserved;
* the Decision supervisor neither opens decisions nor starts model steps;
* ``evaluate_start`` / managed successors refuse (in-process cached value).

It does NOT hold executions (no ``held`` flag is touched) and does not cancel
an already-sent request. Switching it OFF is therefore the only step needed to
resume; there is no per-execution state to unwind. Read-only endpoints, the
observer journal, diagnosis and the watchdog keep running.

The cache below is only an optimisation for synchronous gates; every async
choke point re-reads the durable row.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

GLOBAL_KEY = "global"
REASON = "kill_switch_active"


class KillSwitchActive(RuntimeError):
    """Raised before any network call or charge while the emergency stop is ON."""


class KillSwitchState(Base):
    """Single durable row (key='global'); absent row means OFF."""
    __tablename__ = "supervisor_kill_switch"
    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_by: Mapped[str] = mapped_column(String(120), default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(24), default="operator_api")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KillSwitchHistory(Base):
    """Append-only record of every change."""
    __tablename__ = "supervisor_kill_switch_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean)
    actor: Mapped[str] = mapped_column(String(120))
    rationale: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_cache = {"active": False}


def cached_active() -> bool:
    """Last value seen or written by this process; for synchronous gates only."""
    return bool(_cache["active"])


def reset_cache() -> None:
    _cache["active"] = False


async def ensure_table(master) -> None:
    """Create the two tables if the startup migration did not (idempotent)."""
    async with master() as session:
        def create(sync_session):
            connection = sync_session.connection()
            KillSwitchState.__table__.create(connection, checkfirst=True)
            KillSwitchHistory.__table__.create(connection, checkfirst=True)
        await session.run_sync(create)
        await session.commit()


async def is_active(session) -> bool:
    """Durable read on the caller's session; also refreshes the cache.

    A missing table can only mean the switch was never set (setting creates the
    table first), so it reads as OFF. Any other database error propagates: the
    caller's own queries would fail on the same database anyway.
    """
    try:
        value = await session.scalar(select(KillSwitchState.active).where(
            KillSwitchState.key == GLOBAL_KEY))
    except (OperationalError, ProgrammingError):
        await session.rollback()
        _cache["active"] = False
        return False
    _cache["active"] = bool(value)
    return _cache["active"]


async def guard(session) -> None:
    """Raise KillSwitchActive before any charge / reservation / network call."""
    if await is_active(session):
        raise KillSwitchActive("kill switch active: operator emergency stop blocks supervisor requests")


async def load(master) -> bool:
    """Startup / reload: ensure the table and prime the cache from the database."""
    await ensure_table(master)
    async with master() as session:
        return await is_active(session)


def _json(row, history=None) -> dict:
    data = {"active": bool(row and row.active),
            "changed_by": row.changed_by if row else None,
            "rationale": row.rationale if row else None,
            "source": row.source if row else None,
            "revision": row.revision if row else 0,
            "changed_at": row.changed_at if row else None}
    if history is not None:
        data["history"] = history
    return data


async def status(master, *, history_limit: int = 20) -> dict:
    await ensure_table(master)
    async with master() as session:
        row = await session.scalar(select(KillSwitchState).where(KillSwitchState.key == GLOBAL_KEY))
        _cache["active"] = bool(row and row.active)
        rows = (await session.scalars(select(KillSwitchHistory).order_by(
            KillSwitchHistory.id.desc()).limit(history_limit))).all()
        history = [{"revision": h.revision, "active": h.active, "actor": h.actor,
                    "rationale": h.rationale, "source": h.source, "created_at": h.created_at}
                   for h in rows]
        return _json(row, history)


async def set_state(master, *, active: bool, actor: str, rationale: str,
                    source: str = "operator_api") -> dict:
    """Persist the flag with explicit identity and rationale, and audit it.

    Every managed execution's action history receives an ``ActionRecord`` so the
    stop is visible where operators already look. An unchanged value is a no-op
    (no revision, no audit noise).
    """
    from app.services.actions_v1 import ActionExecutionControl, ActionRecord
    if not str(actor or "").strip() or len((rationale or "").strip()) < 3:
        raise ValueError("explicit operator identity and rationale required")
    await ensure_table(master)
    async with master() as session:
        row = await session.scalar(select(KillSwitchState).where(KillSwitchState.key == GLOBAL_KEY))
        if row is not None and bool(row.active) == bool(active):
            _cache["active"] = bool(row.active)
            return {**_json(row), "changed": False}
        if row is None:
            row = KillSwitchState(key=GLOBAL_KEY, revision=0)
            session.add(row)
        row.active = bool(active)
        row.changed_by = actor.strip()[:120]
        row.rationale = rationale.strip()
        row.source = source[:24]
        row.revision = int(row.revision or 0) + 1
        row.changed_at = datetime.utcnow()
        session.add(KillSwitchHistory(revision=row.revision, active=row.active, actor=row.changed_by,
                                      rationale=row.rationale, source=row.source))
        controls = (await session.scalars(select(ActionExecutionControl).where(
            ActionExecutionControl.enabled.is_(True)))).all()
        for control in controls:
            session.add(ActionRecord(
                action_id=str(uuid.uuid4()), execution_id=control.execution_id,
                project_id=control.project_id,
                action_kind="kill_switch_on" if row.active else "kill_switch_off",
                initiator=row.changed_by, rationale=row.rationale, evidence_ids=[],
                preconditions={"global": True, "revision": row.revision, "source": row.source},
                idempotency_key=f"kill-switch:{row.revision}", status="accepted",
                verification={"business_action": False}))
        await session.commit()
        _cache["active"] = bool(row.active)
        return {**_json(row), "changed": True}
