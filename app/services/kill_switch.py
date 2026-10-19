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
    pass


class KillSwitchState(Base):
    __tablename__ = "supervisor_kill_switch"
    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_by: Mapped[str] = mapped_column(String(120), default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(24), default="operator_api")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KillSwitchHistory(Base):
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
    return bool(_cache["active"])


def reset_cache() -> None:
    _cache["active"] = False


async def ensure_table(master) -> None:
    async with master() as session:
        def create(sync_session):
            connection = sync_session.connection()
            KillSwitchState.__table__.create(connection, checkfirst=True)
            KillSwitchHistory.__table__.create(connection, checkfirst=True)
        await session.run_sync(create)
        await session.commit()


def _is_missing_table(exc: BaseException) -> bool:
    text_ = str(exc).lower()
    return "no such table" in text_ or "does not exist" in text_ or "undefinedtable" in text_


async def is_active(session) -> bool:
    try:
        value = await session.scalar(select(KillSwitchState.active).where(
            KillSwitchState.key == GLOBAL_KEY))
    except (OperationalError, ProgrammingError) as exc:
        await session.rollback()
        if not _is_missing_table(exc):
            raise
        _cache["active"] = False
        return False
    _cache["active"] = bool(value)
    return _cache["active"]


async def guard(session) -> None:
    if await is_active(session):
        raise KillSwitchActive("kill switch active: operator emergency stop blocks supervisor requests")


class KillSwitchCheckFailed(KillSwitchActive):
    pass


async def guard_native(sessions) -> None:
    try:
        async with sessions() as session:
            active = await is_active(session)
    except Exception as exc:
        raise KillSwitchCheckFailed(
            f"kill switch state unreadable ({type(exc).__name__}): paid call refused") from exc
    if active:
        raise KillSwitchActive("kill switch active: operator emergency stop blocks paid calls")


async def load(master) -> bool:
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
