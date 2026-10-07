"""Local money limits for the supervisory layer (no network, no cloud dependency).

A durable append-only ledger (``spend_ledger``, master DB) holds estimated and
recorded spend in USD and credits. ``reserve`` atomically checks three caps and
inserts the estimate in one statement, so two racing reservations can never
both pass a cap that only fits one:

* daily   (all projects, UTC day)   ``SPEND_DAILY_CAP_USD``
* project (all time)                ``SPEND_PROJECT_CAP_USD``
* run     (execution id / plan id)  ``SPEND_RUN_CAP_USD``

A cap of 0 means unlimited. Read-only work never calls the guard.

Native paid seams (manual Studio buttons, pipeline steps, Create routers) pass
``paid_gate`` / ``paid_call`` / ``paid_seam`` BEFORE any request leaves the process:
operator kill switch, disk guard and the same three caps, decided conservatively
(facts + open reserves + rows of unknown cost at a fixed estimate + estimates of calls
still in flight in this process + this call's own estimate). Any failure of the check
itself refuses the call (fail-closed). The remote (cloud) kill switch in api_tracker_hook
is not consulted: this module works offline.
"""
from __future__ import annotations

import asyncio
import contextvars
import math
import os
import re
import time
from contextlib import asynccontextmanager
import functools
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import Float, Integer, String, DateTime, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

# Conservative per-request defaults when no estimate is known (USD).
DEFAULT_REQUEST_ESTIMATE_USD = 0.05      # text request; measured calls cost 0.00005-0.008
MEDIA_SUBMIT_ESTIMATE_USD = 0.30         # paid media submit; status/download/upload are free
PLANNER_ESTIMATE_USD = 0.05
# UNCALIBRATED conservative defaults for native seams whose price is not known before the call.
OUTSEE_VIDEO_ESTIMATE_USD = 1.00         # in-flight estimate of one Outsee video submit
UNKNOWN_ROW_ESTIMATE_USD = 0.30          # each recorded row of unknown cost counts this much in cap decisions
CREDITS_REASON = "credits_exhausted"

REFUSAL_CODES = ("spend_cap_run", "spend_cap_project", "spend_cap_daily")


def _source_ru(source: str) -> str:
    try:
        from app.services.telemetry import spend_source_label_ru
        return spend_source_label_ru(source)
    except Exception:  # noqa: BLE001
        return source


class SpendCapExceeded(RuntimeError):
    """Structured refusal raised before any charge or network call."""

    def __init__(self, code: str, spent: float, cap: float, estimate: float = 0.0, source: str = ""):
        self.code, self.spent, self.cap, self.estimate, self.source = code, spent, cap, estimate, source
        super().__init__(f"{code}: spent {spent:.4f} + estimate {estimate:.4f} exceeds cap {cap:.4f} USD")

    def as_dict(self) -> dict:
        return {"allowed": False, "code": self.code, "spent": round(self.spent, 6),
                "cap": self.cap, "estimate": self.estimate, "source": self.source,
                "source_ru": _source_ru(self.source)}


def _ctx_request_id() -> str:
    from app.services.request_context import get_request_id
    return get_request_id()


def _ctx_run_id() -> str:
    from app.services.request_context import get_run_id
    return get_run_id()


class SpendLedger(Base):
    """Append-only: kind reserve (+estimate), settle (actual-reserved), actual (native), event (0)."""
    __tablename__ = "spend_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    day: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    provider: Mapped[str] = mapped_column(String(32), default="")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    run_id: Mapped[str] = mapped_column(String(80), default=_ctx_run_id, server_default="", index=True)
    source: Mapped[str] = mapped_column(String(32), default="")
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    credits: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(String(200), default="")
    # B02: correlation. request_id = the operator request / worker tick that caused the spend;
    # ref = the provider task id or the plan step ("plan:<id>#<step>"). Added by ALTER on old databases.
    request_id: Mapped[str] = mapped_column(String(64), default=_ctx_request_id, server_default="", index=True)
    ref: Mapped[str] = mapped_column(String(120), default="", server_default="")


_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS spend_ledger_no_update BEFORE UPDATE ON "
    "spend_ledger BEGIN SELECT RAISE(ABORT, 'spend ledger is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS spend_ledger_no_delete BEFORE DELETE ON "
    "spend_ledger BEGIN SELECT RAISE(ABORT, 'spend ledger is append-only'); END",
)
# B02: columns added after the first release. ``ensure_schema`` applies them by ALTER TABLE ADD COLUMN (allowed
# by the append-only triggers: they forbid UPDATE/DELETE of rows, not a new column). The integrator registers the
# same lists as a numbered migration (see ``add_missing_columns``); nothing is dropped or rewritten.
SPEND_ADDED_COLUMNS = (
    ("request_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("ref", "VARCHAR(120) NOT NULL DEFAULT ''"),
)
SPEND_ADDED_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_spend_ledger_request_id ON spend_ledger (request_id)",
)
TASK_ADDED_COLUMNS = (
    ("run_id", "VARCHAR(80) NOT NULL DEFAULT ''"),
    ("request_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
)
TASK_ADDED_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_provider_task_ledger_run_id ON provider_task_ledger (run_id)",
    "CREATE INDEX IF NOT EXISTS ix_provider_task_ledger_request_id ON provider_task_ledger (request_id)",
)
_ensured: set[str] = set()


def add_missing_columns(sync_conn, table: str, columns, indexes=()) -> list[str]:
    """Idempotent ``ALTER TABLE ADD COLUMN`` for a database created before the columns existed.

    ``sync_conn`` is a synchronous SQLAlchemy connection (``await session.run_sync(lambda s:
    add_missing_columns(s.connection(), ...))`` or ``conn.run_sync``). Returns the names added."""
    from sqlalchemy import inspect
    have = {c["name"] for c in inspect(sync_conn).get_columns(table)}
    added: list[str] = []
    for name, ddl in columns:
        if name not in have:
            sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added.append(name)
    for ddl in indexes:
        sync_conn.exec_driver_sql(ddl)
    return added


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def ensure_schema(session) -> None:
    """Idempotent table + append-only triggers on the caller's database."""
    bind = session.get_bind()
    key = str(getattr(bind, "url", id(bind)))
    if key in _ensured:
        return
    await session.run_sync(lambda s: SpendLedger.__table__.create(s.connection(), checkfirst=True))
    await session.run_sync(lambda s: add_missing_columns(
        s.connection(), "spend_ledger", SPEND_ADDED_COLUMNS, SPEND_ADDED_INDEXES))
    if session.get_bind().dialect.name == "sqlite":
        for ddl in _TRIGGERS:
            await session.execute(text(ddl))
    _ensured.add(key)


def caps() -> dict:
    from app.settings import settings
    return {"daily": float(settings.spend_daily_cap_usd),
            "project": float(settings.spend_project_cap_usd),
            "run": float(settings.spend_run_cap_usd)}


async def _sum(session, *, day: str | None = None, project_id: int | None = None,
               run_id: str | None = None) -> float:
    from sqlalchemy import func
    q = select(func.coalesce(func.sum(SpendLedger.usd), 0.0))
    if day is not None:
        q = q.where(SpendLedger.day == day)
    if project_id is not None:
        q = q.where(SpendLedger.project_id == project_id)
    if run_id:
        q = q.where(SpendLedger.run_id == run_id)
    return float(await session.scalar(q) or 0.0)


async def totals(session, project_id: int | None = None, run_id: str = "") -> dict:
    await ensure_schema(session)
    c = caps()
    out = {"caps": c, "day": _today(), "daily_spent": await _sum(session, day=_today())}
    out["project_spent"] = await _sum(session, project_id=project_id) if project_id is not None else None
    out["run_spent"] = await _sum(session, run_id=run_id) if run_id else None
    return out


async def _refusal(session, project_id, run_id, usd, source) -> SpendCapExceeded:
    c = caps()
    day = await _sum(session, day=_today())
    proj = await _sum(session, project_id=project_id) if project_id is not None else 0.0
    run = await _sum(session, run_id=run_id) if run_id else 0.0
    for code, spent, cap in (("spend_cap_run", run, c["run"]), ("spend_cap_project", proj, c["project"]),
                             ("spend_cap_daily", day, c["daily"])):
        if cap > 0 and spent + usd > cap + 1e-9:
            return SpendCapExceeded(code, spent, cap, usd, source)
    return SpendCapExceeded("spend_cap_daily", day, c["daily"], usd, source)


def _valid(usd) -> float:
    usd = float(usd)
    if usd < 0 or math.isnan(usd) or math.isinf(usd):
        raise ValueError("invalid spend estimate")
    return usd


async def check_allowed(project_id: int | None, estimate_usd: float, source: str, *,
                        run_id: str = "", session=None, read_only: bool = False) -> dict:
    """Non-mutating check. Read-only work is always allowed."""
    if read_only:
        return {"allowed": True, "code": "", "read_only": True}
    usd = _valid(estimate_usd)
    if session is None:
        from app.services.action_layer import store
        async with store.sessions()() as own:
            return await check_allowed(project_id, usd, source, run_id=run_id, session=own)
    await ensure_schema(session)
    c = caps()
    for code, spent, cap in (
            ("spend_cap_run", await _sum(session, run_id=run_id) if run_id else 0.0, c["run"]),
            ("spend_cap_project", await _sum(session, project_id=project_id) if project_id is not None else 0.0, c["project"]),
            ("spend_cap_daily", await _sum(session, day=_today()), c["daily"])):
        if cap > 0 and spent + usd > cap + 1e-9:
            return SpendCapExceeded(code, spent, cap, usd, source).as_dict()
    return {"allowed": True, "code": ""}


@dataclass
class Pending:
    usd: float
    project_id: int | None
    run_id: str
    source: str
    provider: str


_pending: contextvars.ContextVar[Pending | None] = contextvars.ContextVar("spend_pending", default=None)


async def reserve(session, *, project_id: int | None, run_id: str, estimate_usd: float,
                  source: str, provider: str = "", credits: float = 0.0, note: str = "",
                  track: bool = True, request_id: str | None = None, ref: str = "") -> float:
    """Atomically check all caps and append a reserve row in the caller's transaction.

    Raises SpendCapExceeded (nothing written). The caller commits. ``request_id`` defaults to the current
    request (``request_context``); ``run_id`` is the cap window and is written as given (caps are unchanged).
    """
    usd = _valid(estimate_usd)
    await ensure_schema(session)
    c = caps()
    rid = (request_id if request_id is not None else _ctx_request_id())[:64]
    res = await session.execute(text(
        "INSERT INTO spend_ledger (created_at, day, kind, provider, project_id, run_id, source, usd, credits, note, "
        "request_id, ref) "
        "SELECT :now, :day, 'reserve', :prov, :pid, :run, :src, :usd, :cr, :note, :rid, :ref WHERE "
        "(:rcap <= 0 OR :run = '' OR COALESCE((SELECT SUM(usd) FROM spend_ledger WHERE run_id = :run), 0) + :usd <= :rcap) AND "
        "(:pcap <= 0 OR :pid IS NULL OR COALESCE((SELECT SUM(usd) FROM spend_ledger WHERE project_id = :pid), 0) + :usd <= :pcap) AND "
        "(:dcap <= 0 OR COALESCE((SELECT SUM(usd) FROM spend_ledger WHERE day = :day), 0) + :usd <= :dcap)"),
        {"now": datetime.utcnow(), "day": _today(), "prov": provider[:32], "pid": project_id,
         "run": run_id[:80], "src": source[:32], "usd": usd, "cr": float(credits), "note": _clean(note),
         "rid": rid, "ref": ref[:120],
         "rcap": c["run"], "pcap": c["project"], "dcap": c["daily"]})
    if res.rowcount != 1:
        raise await _refusal(session, project_id, run_id, usd, source)
    if track:
        _pending.set(Pending(usd, project_id, run_id, source, provider))
    return usd


async def settle(session, *, project_id: int | None, run_id: str, reserved_usd: float,
                 actual_usd: float | None, source: str, provider: str = "",
                 credits_delta: float = 0.0, basis: str = "", request_id: str | None = None,
                 ref: str = "") -> None:
    """Append actual - reserved; unknown actual leaves the reserve (conservative).

    ``basis`` (B03) says where the amount came from: ``reported`` (the provider's own answer or a measured
    balance change) makes the settle row an ``actual`` in the unified model; ``estimate`` (or empty, the old
    rows) keeps it an ``estimated``."""
    if actual_usd is None or actual_usd < 0 or math.isnan(actual_usd):
        return
    delta = float(actual_usd) - float(reserved_usd)
    if delta:
        await ensure_schema(session)
        session.add(SpendLedger(day=_today(), kind="settle", provider=provider[:32],
                                project_id=project_id, run_id=(run_id or _ctx_run_id())[:80], source=source[:32],
                                usd=delta, credits=credits_delta, note=_basis_note(basis),
                                request_id=(request_id if request_id is not None else _ctx_request_id())[:64],
                                ref=ref[:120]))


def _clean(note: str) -> str:
    """Notes are free text from callers: secrets never reach the append-only ledger."""
    try:
        from app.services.redact import redact_text

        return redact_text(str(note or ""))[:200]
    except Exception:  # noqa: BLE001
        return str(note or "")[:200]


def _basis_note(basis: str) -> str:
    return f"basis={basis}" if basis else ""


async def record_actual(session, *, project_id: int | None, usd: float, provider: str,
                        source: str = "native_call", run_id: str = "", note: str = "",
                        credits: float = 0.0, kind: str = "actual", request_id: str | None = None,
                        ref: str = "") -> None:
    """Record a call that was never reserved (native pipeline). Never refuses.

    ``kind="actual"`` only for a fact (the provider's answer or a measurement). A price computed by us is
    ``kind="estimated"`` (B03)."""
    usd = _valid(usd)
    await ensure_schema(session)
    session.add(SpendLedger(day=_today(), kind=kind[:12], provider=provider[:32], project_id=project_id,
                            run_id=(run_id or _ctx_run_id())[:80], source=source[:32], usd=usd,
                            credits=float(credits), note=_clean(note),
                            request_id=(request_id if request_id is not None else _ctx_request_id())[:64],
                            ref=ref[:120]))


async def record_event(session, *, project_id: int | None, provider: str, source: str, note: str = "",
                       run_id: str = "", request_id: str | None = None, ref: str = "") -> None:
    """Zero-USD audit row (kind event)."""
    await ensure_schema(session)
    session.add(SpendLedger(day=_today(), kind="event", provider=provider[:32], project_id=project_id,
                            run_id=(run_id or _ctx_run_id())[:80], source=source[:32], note=_clean(note),
                            request_id=(request_id if request_id is not None else _ctx_request_id())[:64],
                            ref=ref[:120]))


class ProviderTaskLedger(Base):
    """Append-only event log of provider task ids. The state of a task is its newest row."""
    __tablename__ = "provider_task_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), default="")
    task_id: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    api: Mapped[str] = mapped_column(String(16), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    event: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(16), default="")
    est_credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    est_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    # B02: the run and request this task belongs to (same values as the money rows of the same call).
    run_id: Mapped[str] = mapped_column(String(80), default=_ctx_run_id, server_default="", index=True)
    request_id: Mapped[str] = mapped_column(String(64), default=_ctx_request_id, server_default="", index=True)


_TASK_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS provider_task_ledger_no_update BEFORE UPDATE ON "
    "provider_task_ledger BEGIN SELECT RAISE(ABORT, 'provider task ledger is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS provider_task_ledger_no_delete BEFORE DELETE ON "
    "provider_task_ledger BEGIN SELECT RAISE(ABORT, 'provider task ledger is append-only'); END",
)
_task_ensured: set[str] = set()


async def ensure_task_schema(session) -> None:
    bind = session.get_bind()
    key = str(getattr(bind, "url", id(bind)))
    if key in _task_ensured:
        return
    await session.run_sync(lambda s: ProviderTaskLedger.__table__.create(s.connection(), checkfirst=True))
    await session.run_sync(lambda s: add_missing_columns(
        s.connection(), "provider_task_ledger", TASK_ADDED_COLUMNS, TASK_ADDED_INDEXES))
    if session.get_bind().dialect.name == "sqlite":
        for ddl in _TASK_TRIGGERS:
            await session.execute(text(ddl))
    _task_ensured.add(key)


async def append_task_event(session, *, project_id: int | None, key: str, task_id: str, provider: str,
                            api: str, model: str, event: str, status: str,
                            est_credits: float | None = None, est_usd: float | None = None,
                            note: str = "", run_id: str = "", request_id: str | None = None) -> None:
    """Append one event; the caller commits. Never updates an existing row.

    ``run_id`` / ``request_id`` default to the current correlation (B02), so a task row and the money row of
    the same call carry the same ids."""
    await ensure_task_schema(session)
    session.add(ProviderTaskLedger(project_id=project_id, key=key[:120], task_id=str(task_id)[:100],
                                   provider=provider[:32], api=api[:16], model=model[:64], event=event[:16],
                                   status=status[:16], est_credits=est_credits, est_usd=est_usd, note=_clean(note),
                                   run_id=(run_id or _ctx_run_id())[:80],
                                   request_id=(request_id if request_id is not None else _ctx_request_id())[:64]))


async def list_provider_tasks(session, project_id: int, limit: int = 200) -> list[dict]:
    """One row per task: first-seen time and estimate, newest status, event count."""
    await ensure_task_schema(session)
    rows = (await session.scalars(select(ProviderTaskLedger).where(
        ProviderTaskLedger.project_id == project_id).order_by(ProviderTaskLedger.id))).all()
    out: dict[str, dict] = {}
    for r in rows:
        t = out.setdefault(r.task_id, {"task_id": r.task_id, "key": r.key, "provider": r.provider,
                                       "api": r.api, "model": r.model, "submitted_at": r.created_at.isoformat(),
                                       "est_credits": r.est_credits, "est_usd": r.est_usd, "events": 0})
        t["events"] += 1
        t["status"], t["updated_at"], t["note"] = r.status, r.created_at.isoformat(), r.note
    return list(out.values())[-max(1, limit):]


INTENT_PREFIX = "intent:"
OPEN_TASK_STATUSES = frozenset({"submitted", "pending", "waiting", "queuing", "generating", "unknown", "succeeded"})
_PH_RE = re.compile(r"\bph=([0-9a-f]{6,32})\b")


def intent_task_id(intent_id: str) -> str:
    return f"{INTENT_PREFIX}{intent_id}"


async def find_task_states(session, *, project_id: int | None, key: str, model: str | None,
                           since: datetime) -> list[dict]:
    """Tasks and submit intents of one frame key submitted at or after ``since``.

    Each entry: task_id, status (newest row), intent (bool), ph (prompt hash from a note), api,
    first_at. A task's newest row may come from another writer, so states are read by task id.
    """
    await ensure_task_schema(session)
    q = select(ProviderTaskLedger.task_id).where(
        ProviderTaskLedger.project_id == project_id, ProviderTaskLedger.key == key[:120],
        ProviderTaskLedger.created_at >= since)
    if model is not None:
        q = q.where(ProviderTaskLedger.model == model[:64])
    recent = (await session.scalars(q.distinct())).all()
    if not recent:
        return []
    rows = (await session.scalars(select(ProviderTaskLedger).where(
        ProviderTaskLedger.task_id.in_(list(recent))).order_by(ProviderTaskLedger.id))).all()
    out: dict[str, dict] = {}
    for r in rows:
        t = out.setdefault(r.task_id, {"task_id": r.task_id, "intent": r.task_id.startswith(INTENT_PREFIX),
                                       "ph": "", "api": r.api, "model": r.model, "first_at": r.created_at, "status": ""})
        m = _PH_RE.search(r.note or "")
        if m and not t["ph"]:
            t["ph"] = m.group(1)
        t["status"] = r.status
    return [t for t in out.values() if t["first_at"] >= since]


async def append_intent(session, *, project_id: int | None, key: str, model: str, api: str,
                        intent_id: str, ph: str) -> None:
    """Row written BEFORE a createTask POST: the outcome is unknown until it is resolved."""
    await append_task_event(session, project_id=project_id, key=key, task_id=intent_task_id(intent_id),
                            provider="kie", api=api, model=model, event="intent", status="submit_unknown",
                            note=f"ph={ph}")


async def resolve_intent(session, *, project_id: int | None, key: str, model: str, api: str,
                         intent_id: str, status: str, note: str = "") -> None:
    """``bound`` (task id received) or ``rejected`` (provider did not accept / request never sent)."""
    await append_task_event(session, project_id=project_id, key=key, task_id=intent_task_id(intent_id),
                            provider="kie", api=api, model=model, event="intent_done", status=status, note=note)


_tasks: set = set()


def schedule_actual(*, project_id: int | None, usd: float, provider: str, reported: bool = False) -> None:
    """Sync entry for _track_api_cost: settle the pending reservation or record a native call.

    Fire and forget; never raises and never blocks the caller. ``usd`` is a price computed from token counts
    (tracker rates), so by default it is an ESTIMATE in the unified model (B03); pass ``reported=True`` only
    when the provider itself sent the amount. The correlation (project, run, request) is captured here, in the
    caller's context, not later in the background task.
    """
    try:
        pending = _pending.get()
        _pending.set(None)
        loop = asyncio.get_running_loop()
        corr = {"run_id": _ctx_run_id(), "request_id": _ctx_request_id()}
        if project_id is None:
            from app.services.request_context import get_project_id
            project_id = get_project_id()
        task = loop.create_task(_write_actual(pending, project_id, float(usd), provider, reported, corr))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:  # noqa: BLE001 - recording must never affect the real request
        _note_write_failure("schedule_actual")


_write_failures = {"count": 0, "last": ""}


def write_failure_count() -> int:
    """Ledger writes of this module that failed in this process (a visible hole counter)."""
    return int(_write_failures["count"])


def _note_write_failure(where: str) -> None:
    _write_failures["count"] += 1
    _write_failures["last"] = where
    try:
        logger.exception("spend_guard: ledger write failed ({}); failures so far: {}",
                         where, _write_failures["count"])
    except Exception:  # noqa: BLE001
        pass


def _price_note(provider: str, reported: bool) -> str:
    """Why a computed price is an estimate: Vibecode rates are not calibrated against its balance."""
    if reported:
        return "basis=reported"
    return "basis=computed uncalibrated" if str(provider or "").lower() == "vibecode" else "basis=computed"


async def _write_actual(pending, project_id, usd, provider, reported=False, corr=None) -> None:
    corr = corr or {}
    try:
        from app.db import SessionLocal
        async with SessionLocal() as s:
            if pending is not None:
                await settle(s, project_id=pending.project_id, run_id=pending.run_id or corr.get("run_id", ""),
                             reserved_usd=pending.usd, actual_usd=usd, source=pending.source,
                             provider=provider or pending.provider, basis="reported" if reported else "estimate",
                             request_id=corr.get("request_id"))
            else:
                await record_actual(s, project_id=project_id, usd=usd, provider=provider,
                                    kind="actual" if reported else "estimated",
                                    note=_price_note(provider, reported), run_id=corr.get("run_id", ""),
                                    request_id=corr.get("request_id"))
            await s.commit()
    except Exception:  # noqa: BLE001
        _note_write_failure("write_actual")


_CREDIT_CODES = {"gpt_credits", "media_credits"}


async def engage_on_credit_error(exc: BaseException, *, master=None, provider: str = "") -> bool:
    """HTTP 402 / credit-exhaustion error from any provider -> engage the supervisory kill switch.

    Only when SPEND_AUTO_KILL_ON_402 is true. The switch covers supervisory work only
    (managed requests, decisions, recovery, action layer); unmanaged native pipeline
    work is not stopped by it. Returns True when the switch was set by this call.
    """
    try:
        from app.settings import settings
        if not settings.spend_auto_kill_on_402:
            return False
        from app.services.error_catalog import describe_error
        if describe_error(exc)[0] not in _CREDIT_CODES:
            return False
        from app.services import kill_switch
        if master is None:
            from app.db import SessionLocal as master
        result = await kill_switch.set_state(
            master, active=True, actor="spend_guard",
            rationale=f"{CREDITS_REASON}: provider {provider or 'unknown'} returned HTTP 402",
            source="spend_guard")
        if result.get("changed"):
            async with master() as s:
                await ensure_schema(s)
                s.add(SpendLedger(day=_today(), kind="event", provider=provider[:32],
                                  source="credit_error", note=CREDITS_REASON))
                await s.commit()
        return bool(result.get("changed"))
    except Exception:  # noqa: BLE001 - never mask the original provider error
        # The kill switch could NOT be engaged after a credit error: this must be visible.
        _note_write_failure("engage_on_credit_error")
        return False


# ---------------------------------------------------------------------------
# Native paid seams: kill switch + disk + money cap BEFORE the request (A07/A09)
# ---------------------------------------------------------------------------

BLOCK_KILL_SWITCH = "kill_switch_active"
BLOCK_CHECK_FAILED = "gate_check_failed"
BLOCK_DISK = "disk_guard_blocked"
PAID_BLOCK_CODES = (BLOCK_KILL_SWITCH, BLOCK_CHECK_FAILED, BLOCK_DISK) + REFUSAL_CODES

_CAP_LABEL_RU = {"spend_cap_run": "за запуск", "spend_cap_project": "по проекту", "spend_cap_daily": "за сутки"}


class PaidCallBlocked(RuntimeError):
    """A native paid request was refused BEFORE it was sent (nothing left the process).

    ``context`` carries ``no_resubmit`` so the video/image retry ladders stop instead of burning
    attempts, and ``request_sent=False`` so nothing treats the outcome as unknown.
    """

    def __init__(self, code: str, message_ru: str, *, provider: str = "", what: str = "",
                 detail: dict | None = None):
        super().__init__(message_ru)
        self.code, self.message_ru = code, message_ru
        self.provider, self.what = provider, what
        self.detail = dict(detail or {})
        self.context = {"no_resubmit": True, "paid_gate_blocked": True, "request_sent": False,
                        "gate_code": code, "provider": provider}

    def as_dict(self) -> dict:
        return {"allowed": False, "code": self.code, "message_ru": self.message_ru,
                "provider": self.provider, "what": self.what, **self.detail}


def _env_float(name: str, default: float) -> float:
    try:
        raw = (os.environ.get(name) or "").strip().replace(",", ".")
        value = float(raw) if raw else default
        return value if value >= 0 and not math.isnan(value) else default
    except ValueError:
        return default


def unknown_row_estimate_usd() -> float:
    """Estimate added to cap decisions for each recorded row of unknown cost."""
    return _env_float("PAID_GATE_UNKNOWN_ROW_USD", UNKNOWN_ROW_ESTIMATE_USD)


def _sessions():
    """Session factory used by the gate; tests replace this function."""
    from app.db import SessionLocal
    return SessionLocal


@dataclass
class _InFlight:
    usd: float
    project_id: int | None
    run_id: str
    day: str
    provider: str
    what: str


_inflight: list[_InFlight] = []
_seam: contextvars.ContextVar[str | None] = contextvars.ContextVar("spend_paid_seam", default=None)
_block_event_stamp: dict[tuple, float] = {}
BLOCK_EVENT_MIN_INTERVAL_S = 60.0


def inflight_usd(*, project_id: int | None = None, run_id: str = "", day: str | None = None) -> dict:
    """Estimates of paid calls that passed the gate in this process and have not finished."""
    day = day or _today()
    return {
        "daily": sum(i.usd for i in _inflight if i.day == day),
        "project": sum(i.usd for i in _inflight if project_id is not None and i.project_id == project_id),
        "run": sum(i.usd for i in _inflight if run_id and i.run_id == run_id),
        "count": len(_inflight),
    }


def reset_inflight() -> None:
    _inflight.clear()
    _block_event_stamp.clear()


def _missing_table(exc: BaseException) -> bool:
    low = str(exc).lower()
    return "no such table" in low or "does not exist" in low


async def conservative_sums(session, *, project_id: int | None, run_id: str) -> dict:
    """Read-only. Facts + open reserves + estimates (every row of ``spend_ledger``, whatever its kind) plus
    a fixed estimate for each recorded row of unknown cost (usd 0 and ``cost_unknown=true``), per window. A missing table reads as zero (nothing spent yet);
    any other database error propagates (the caller refuses the call)."""
    unit = unknown_row_estimate_usd()
    sql = ("SELECT COALESCE(SUM(usd), 0.0), COALESCE(SUM(CASE WHEN kind IN ('actual', 'estimated') AND usd = 0 "
           "AND note LIKE '%cost_unknown=true%' THEN 1 ELSE 0 END), 0) FROM spend_ledger WHERE ")
    windows = (("daily", "day = :v", _today()),
               ("project", "project_id = :v", project_id),
               ("run", "run_id = :v", run_id))
    out: dict[str, float] = {}
    for name, clause, value in windows:
        if (name == "project" and value is None) or (name == "run" and not value):
            out[name] = 0.0
            continue
        try:
            row = (await session.execute(text(sql + clause), {"v": value})).one()
        except (OperationalError, ProgrammingError) as exc:
            await session.rollback()
            if not _missing_table(exc):
                raise
            out[name] = 0.0
            continue
        out[name] = float(row[0] or 0.0) + int(row[1] or 0) * unit
    return out


def _resolve_project(project_id: int | None) -> int | None:
    if project_id is not None:
        return project_id
    try:
        from app.services.request_context import get_project_id  # _cost_project first, then the scope variable
        return get_project_id()
    except Exception:  # noqa: BLE001
        return None


def _disk_verdict(provider: str, what: str) -> None:
    """Soft hook to ``disk_guard.check()``: no module / no function = allowed (the integrator wires it);
    a check that raises or answers "not allowed" blocks."""
    try:
        from app.services import disk_guard
    except ImportError:
        return
    fn = getattr(disk_guard, "check", None)
    if fn is None:
        return
    try:
        verdict = fn()
    except Exception as exc:  # noqa: BLE001 - a broken check is a refusal, not a pass
        raise PaidCallBlocked(
            BLOCK_CHECK_FAILED,
            f"Платный вызов {provider} не отправлен: проверка свободного места на диске завершилась ошибкой "
            f"({type(exc).__name__}). Запрос не отправлялся.", provider=provider, what=what) from exc
    allowed = getattr(verdict, "allowed", None)
    if allowed is None and isinstance(verdict, dict):
        allowed = verdict.get("allowed", verdict.get("ok"))
    if allowed is None and isinstance(verdict, bool):
        allowed = verdict
    if allowed is False:
        reason = getattr(verdict, "reason", "") or (verdict.get("reason", "") if isinstance(verdict, dict) else "")
        raise PaidCallBlocked(
            BLOCK_DISK,
            f"Платный вызов {provider} не отправлен: мало места на диске. {reason} Запрос не отправлялся.",
            provider=provider, what=what, detail={"level": str(getattr(verdict, "level", "") or "")})


async def _record_block(code: str, provider: str, what: str, project_id: int | None) -> None:
    """One zero-USD audit row per (code, provider) per interval; failures are logged, never raised."""
    key = (code, provider)
    now = time.monotonic()
    last = _block_event_stamp.get(key)
    if last is not None and now - last < BLOCK_EVENT_MIN_INTERVAL_S:
        return
    _block_event_stamp[key] = now
    try:
        async with _sessions()() as s:
            await record_event(s, project_id=project_id, provider=provider, source="paid_gate_block",
                               note=f"{code} {what}"[:200])
            await s.commit()
    except Exception:  # noqa: BLE001
        _note_write_failure("paid_gate_block_event")


class PaidTicket:
    """Handle of one call that passed the gate; ``release`` removes its in-flight estimate."""

    def __init__(self, entry: _InFlight | None, usd: float):
        self._entry, self.estimate_usd = entry, usd

    def release(self) -> None:
        entry, self._entry = self._entry, None
        if entry is not None:
            try:
                _inflight.remove(entry)
            except ValueError:
                pass


def _default_estimate(estimate_usd) -> float:
    try:
        usd = float(estimate_usd) if estimate_usd is not None else 0.0
    except (TypeError, ValueError):
        usd = 0.0
    if usd <= 0 or math.isnan(usd) or math.isinf(usd):
        usd = MEDIA_SUBMIT_ESTIMATE_USD
    return usd


async def paid_gate(*, provider: str, what: str = "submit", estimate_usd: float | None = None,
                    project_id: int | None = None, run_id: str = "", register: bool = True) -> PaidTicket:
    """Refuse a native paid request before it is sent, or return a ticket for it.

    Order: operator kill switch (durable read), disk guard, then the three caps against
    ``conservative_sums`` + in-flight estimates + this call's estimate. Raises ``PaidCallBlocked``;
    every failure of the check itself raises it too (fail-closed). Inside an already registered seam of
    the same provider (a nested gate) only the kill switch and the disk are re-checked, so one logical
    call is never counted twice.
    """
    pid = _resolve_project(project_id)
    try:
        return await _paid_gate(provider, what, estimate_usd, pid, run_id, register)
    except PaidCallBlocked as blocked:
        logger.warning("paid gate BLOCKED {} {}: {} ({})", provider, what, blocked.code, blocked.message_ru)
        await _record_block(blocked.code, provider, what, pid)
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail-closed
        blocked = PaidCallBlocked(
            BLOCK_CHECK_FAILED,
            f"Платный вызов {provider} не отправлен: не удалось проверить рубильник и потолок трат "
            f"({type(exc).__name__}). Запрос не отправлялся.", provider=provider, what=what)
        logger.exception("paid gate check failed {} {}", provider, what)
        await _record_block(blocked.code, provider, what, pid)
        raise blocked from exc


async def _paid_gate(provider, what, estimate_usd, pid, run_id, register) -> PaidTicket:
    from app.services import kill_switch
    try:
        await kill_switch.guard_native(_sessions())
    except kill_switch.KillSwitchCheckFailed as exc:
        raise PaidCallBlocked(
            BLOCK_CHECK_FAILED,
            f"Платный вызов {provider} не отправлен: состояние аварийного рубильника не удалось прочитать. "
            "Запрос не отправлялся.", provider=provider, what=what) from exc
    except kill_switch.KillSwitchActive as exc:
        raise PaidCallBlocked(
            BLOCK_KILL_SWITCH,
            f"Платный вызов {provider} заблокирован аварийным рубильником. Запрос не отправлялся.",
            provider=provider, what=what) from exc
    _disk_verdict(provider, what)
    if _seam.get() == provider:
        return PaidTicket(None, 0.0)  # nested inside a registered seam of the same provider
    usd = _default_estimate(estimate_usd)
    c = caps()
    run_id = (run_id or "")[:80]
    windows = (("spend_cap_run", "run", bool(run_id)),
               ("spend_cap_project", "project", pid is not None),
               ("spend_cap_daily", "daily", True))
    if any(c[key] > 0 and applies for _code, key, applies in windows):
        async with _sessions()() as session:
            sums = await conservative_sums(session, project_id=pid, run_id=run_id)
        # No await between this read of the in-flight list and the registration below: the decision
        # and the in-flight entry are atomic with respect to other coroutines of this process.
        fly = inflight_usd(project_id=pid, run_id=run_id)
        for code, key, applies in windows:
            cap = c[key]
            spent = sums[key] + fly[key]
            if applies and cap > 0 and spent + usd > cap + 1e-9:
                raise PaidCallBlocked(
                    code,
                    f"Платный вызов {provider} не отправлен: потолок трат {_CAP_LABEL_RU[code]} "
                    f"({cap:.2f} USD) был бы превышен (учтено {spent:.2f} USD с открытыми резервами "
                    f"и оценками, этот вызов {usd:.2f} USD). Запрос не отправлялся.",
                    provider=provider, what=what,
                    detail={"spent": round(spent, 6), "cap": cap, "estimate": usd, "source": what})
    entry = None
    if register:
        entry = _InFlight(usd, pid, run_id, _today(), provider, what)
        _inflight.append(entry)
    return PaidTicket(entry, usd)


@asynccontextmanager
async def paid_call(provider: str, what: str = "submit", *, estimate_usd: float | None = None,
                    project_id: int | None = None, run_id: str = "", wrap_error=None):
    """``async with paid_call(...)``: gate, then hold the in-flight estimate while the request runs.

    ``wrap_error`` (optional) turns ``PaidCallBlocked`` into the provider's own error class so the
    existing handling of that provider shows the refusal in the usual place.
    """
    try:
        ticket = await paid_gate(provider=provider, what=what, estimate_usd=estimate_usd,
                                 project_id=project_id, run_id=run_id)
    except PaidCallBlocked as blocked:
        if wrap_error is None:
            raise
        raise wrap_error(blocked) from blocked
    token = _seam.set(provider)
    try:
        yield ticket
    finally:
        _seam.reset(token)
        ticket.release()


def paid_seam(provider: str, what: str = "submit", *, estimate=None, wrap_error=None):
    """Decorator for an async function that sends one paid request.

    ``estimate``: a number, or ``callable(args, kwargs) -> float | None``. Put it OUTSIDE other
    decorators (recording, observation) so a refusal is not recorded as a failed paid call.
    """
    def decorate(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            est = estimate(args, kwargs) if callable(estimate) else estimate
            async with paid_call(provider, what, estimate_usd=est, wrap_error=wrap_error):
                return await fn(*args, **kwargs)
        wrapper.__paid_seam__ = (provider, what)
        return wrapper
    return decorate


async def paid_precheck(provider: str, what: str = "submit", *, estimate_usd: float | None = None,
                        project_id: int | None = None, run_id: str = "") -> dict:
    """Same decision as ``paid_gate`` without keeping an in-flight estimate (for a route that only
    enqueues work and wants to refuse at once). Returns ``{"allowed": True}`` or the block as a dict."""
    try:
        ticket = await paid_gate(provider=provider, what=what, estimate_usd=estimate_usd,
                                 project_id=project_id, run_id=run_id, register=False)
        ticket.release()
        return {"allowed": True, "code": ""}
    except PaidCallBlocked as blocked:
        return blocked.as_dict()


# ---------------------------------------------------------------------------
# Unified spend model (B03) and correlation chain (B02)
# ---------------------------------------------------------------------------
# One vocabulary for money, whatever table or path wrote it:
#   estimated  a price we computed or could not know (catalog, constant, tokens x rate, "cost unknown")
#   reserved   an open reserve held before a call (the action layer, the planner, a managed run)
#   actual     a fact: the provider's own answer/amount, a measured balance change, or a definite refusal (0 USD)
#   gap        a lost record (``write_failed``): the money is not in the ledger and must be restored by hand
#   event      other zero-USD audit rows (kill switch, 402, a blocked paid call)
# Common fields of every row: project_id, run_id (+ execution_id when the run is a managed execution),
# request_id, ref, provider, source, amount_usd, credits.

UNIFIED_KINDS = ("estimated", "reserved", "actual", "gap", "event")
_NOT_A_FACT_MARKS = ("uncalibrated", "cost_unknown", "basis=computed", "basis=estimate")
_RUN_PREFIXES = ("plan:", "native:", "regen:", "queue:")


def unified_kind(kind: str | None, source: str | None = "", note: str | None = "") -> str:
    """Map a stored ``spend_ledger`` row to the unified kind. An ``actual`` that carries an uncalibrated /
    unknown-cost / computed mark is NOT a fact (old rows written before B03), so it reads as ``estimated``."""
    kind, source, low = (kind or ""), (source or ""), (note or "").lower()
    if kind == "event":
        return "gap" if source == "write_failed" else "event"
    if kind == "reserve":
        return "reserved"
    if kind == "settle":
        if "basis=closes_reserve" in low:
            return "reserved"  # nets an open reserve to zero; the money itself is in the native rows
        return "actual" if "basis=reported" in low else "estimated"
    if kind == "actual":
        return "estimated" if any(m in low for m in _NOT_A_FACT_MARKS) else "actual"
    return "estimated"


def execution_of(run_id: str | None) -> str | None:
    """``run_id`` is an execution id unless it is a plan (``plan:<id>``) or a native tick (``native:..``)."""
    run_id = (run_id or "").strip()
    return run_id if run_id and not run_id.startswith(_RUN_PREFIXES) else None


def unified_row(row) -> dict:
    created = getattr(row, "created_at", None)
    return {
        "id": row.id, "ts": created.isoformat() if created else None,
        "project_id": row.project_id, "run_id": row.run_id or "", "execution_id": execution_of(row.run_id),
        "request_id": getattr(row, "request_id", "") or "", "ref": getattr(row, "ref", "") or "",
        "provider": row.provider or "", "source": row.source or "", "ledger_kind": row.kind,
        "kind": unified_kind(row.kind, row.source, row.note), "amount_usd": float(row.usd or 0.0),
        "credits": float(row.credits or 0.0), "note": row.note or "",
    }


async def unified_rows(session, *, project_id: int | None = None, run_id: str = "", request_id: str = "",
                       limit: int = 500) -> list[dict]:
    """Row-by-row view in the unified model, oldest first (B06 will show it as the "what was charged for" list)."""
    await ensure_schema(session)
    q = select(SpendLedger)
    if project_id is not None:
        q = q.where(SpendLedger.project_id == project_id)
    if run_id:
        q = q.where(SpendLedger.run_id == run_id)
    if request_id:
        q = q.where(SpendLedger.request_id == request_id)
    rows = (await session.scalars(q.order_by(SpendLedger.id.desc()).limit(max(1, int(limit))))).all()
    return [unified_row(r) for r in reversed(rows)]


async def run_totals(session, run_id: str) -> dict:
    """Totals of one run by unified kind. ``total_usd`` is the logical spend of the run: facts + still open
    reserves + estimates, with a closed reserve netted to zero by its settle row (one call is counted once)."""
    rows = await unified_rows(session, run_id=run_id, limit=100000)
    out: dict = {"run_id": run_id, "rows": len(rows), "total_usd": 0.0,
                 **{f"{k}_usd": 0.0 for k in UNIFIED_KINDS}}
    for r in rows:
        out[f"{r['kind']}_usd"] += r["amount_usd"]
        out["total_usd"] += r["amount_usd"]
    out["gaps"] = sum(1 for r in rows if r["kind"] == "gap")
    return {k: (round(v, 9) if isinstance(v, float) else v) for k, v in out.items()}


async def correlation_gaps(session, *, since: datetime | None = None) -> list[dict]:
    """Money rows that cannot be traced: no project, or neither a run nor a request id.

    Zero-USD ``event`` rows are audit lines and are not counted. An empty result is the B02 law
    "every spend has a project and a run/request"."""
    await ensure_schema(session)
    q = select(SpendLedger).where(SpendLedger.kind != "event")
    if since is not None:
        q = q.where(SpendLedger.created_at >= since)
    rows = (await session.scalars(q.order_by(SpendLedger.id))).all()
    bad = [r for r in rows if r.project_id is None or not (r.run_id or getattr(r, "request_id", ""))]
    return [unified_row(r) for r in bad]


def _present(sync_conn, table: str) -> set[str]:
    from sqlalchemy import inspect
    insp = inspect(sync_conn)
    return {c["name"] for c in insp.get_columns(table)} if insp.has_table(table) else set()


def _pick(cols: set[str], *names: str, default: str = "NULL") -> str:
    for n in names:
        if n in cols:
            return n
    return default


def _chain_sql(sync_conn) -> str:
    """One UNION ALL over the tables that exist and carry ``request_id`` (operator audit, action history,
    money, provider tasks). Columns that a table lacks are NULL, so the shape stays the same."""
    parts: list[str] = []
    audit = _present(sync_conn, "operator_audit")
    if "request_id" in audit:
        parts.append(
            f"SELECT 'operator' AS stage, {_pick(audit, 'ts', 'created_at')} AS at, request_id, "
            f"{_pick(audit, 'project_id')} AS project_id, '' AS run_id, '' AS ref, "
            f"COALESCE({_pick(audit, 'summary_ru', 'path', default=chr(39) * 2)}, '') AS what, "
            "0.0 AS usd, '' AS kind FROM operator_audit WHERE request_id = :rid")
    hist = _present(sync_conn, "action_layer_history")
    if "request_id" in hist:
        parts.append(
            f"SELECT 'plan_step' AS stage, {_pick(hist, 'created_at', 'ts')} AS at, request_id, "
            f"{_pick(hist, 'project_id')} AS project_id, "
            f"COALESCE({_pick(hist, 'execution_id', default=chr(39) * 2)}, '') AS run_id, "
            "COALESCE(plan_id, '') || '#' || COALESCE(step_index, '') AS ref, "
            f"COALESCE({_pick(hist, 'event', default=chr(39) * 2)}, '') AS what, 0.0 AS usd, '' AS kind "
            "FROM action_layer_history WHERE request_id = :rid")
    parts.append(
        "SELECT 'money' AS stage, created_at AS at, request_id, project_id, run_id, ref, "
        "COALESCE(provider, '') || ' ' || COALESCE(note, '') AS what, usd, kind "
        "FROM spend_ledger WHERE request_id = :rid")
    tasks = _present(sync_conn, "provider_task_ledger")
    if "request_id" in tasks:
        parts.append(
            "SELECT 'provider_task' AS stage, created_at AS at, request_id, project_id, run_id, task_id AS ref, "
            "COALESCE(provider, '') || ' ' || COALESCE(event, '') || ' ' || COALESCE(status, '') AS what, "
            "COALESCE(est_usd, 0.0) AS usd, 'task' AS kind FROM provider_task_ledger WHERE request_id = :rid")
    return " UNION ALL ".join(parts) + " ORDER BY at, stage"


async def correlation_chain(session, request_id: str) -> list[dict]:
    """The chain operator -> plan -> step -> request -> task_id -> money for one ``request_id``, one query.

    Stages: ``operator`` (operator_audit, W2-2), ``plan_step`` (action_layer_history once it has the
    ``request_id`` column, W2-1), ``money`` (spend_ledger), ``provider_task`` (provider_task_ledger)."""
    if not request_id:
        return []
    await ensure_schema(session)
    await ensure_task_schema(session)
    sql = await session.run_sync(lambda s: _chain_sql(s.connection()))
    result = await session.execute(text(sql), {"rid": request_id})
    return [dict(r._mapping) for r in result]
