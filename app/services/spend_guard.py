"""Local money limits for the supervisory layer (no network, no cloud dependency).

A durable append-only ledger (``spend_ledger``, master DB) holds estimated and
recorded spend in USD and credits. ``reserve`` atomically checks three caps and
inserts the estimate in one statement, so two racing reservations can never
both pass a cap that only fits one:

* daily   (all projects, UTC day)   ``SPEND_DAILY_CAP_USD``
* project (all time)                ``SPEND_PROJECT_CAP_USD``
* run     (execution id / plan id)  ``SPEND_RUN_CAP_USD``

A cap of 0 means unlimited. Read-only work never calls the guard. Native
pipeline calls are only RECORDED (they count toward totals) and are never
refused here. The remote kill switch in api_tracker_hook is not consulted: this
module works offline.
"""
from __future__ import annotations

import asyncio
import contextvars
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Float, Integer, String, DateTime, select, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

# Conservative per-request defaults when no estimate is known (USD).
DEFAULT_REQUEST_ESTIMATE_USD = 0.05      # text request; measured calls cost 0.00005-0.008
MEDIA_SUBMIT_ESTIMATE_USD = 0.30         # paid media submit; status/download/upload are free
PLANNER_ESTIMATE_USD = 0.05
CREDITS_REASON = "credits_exhausted"

REFUSAL_CODES = ("spend_cap_run", "spend_cap_project", "spend_cap_daily")


class SpendCapExceeded(RuntimeError):
    """Structured refusal raised before any charge or network call."""

    def __init__(self, code: str, spent: float, cap: float, estimate: float = 0.0, source: str = ""):
        self.code, self.spent, self.cap, self.estimate, self.source = code, spent, cap, estimate, source
        super().__init__(f"{code}: spent {spent:.4f} + estimate {estimate:.4f} exceeds cap {cap:.4f} USD")

    def as_dict(self) -> dict:
        return {"allowed": False, "code": self.code, "spent": round(self.spent, 6),
                "cap": self.cap, "estimate": self.estimate, "source": self.source}


class SpendLedger(Base):
    """Append-only: kind reserve (+estimate), settle (actual-reserved), actual (native), event (0)."""
    __tablename__ = "spend_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    day: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    provider: Mapped[str] = mapped_column(String(32), default="")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    run_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    source: Mapped[str] = mapped_column(String(32), default="")
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    credits: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(String(200), default="")


_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS spend_ledger_no_update BEFORE UPDATE ON "
    "spend_ledger BEGIN SELECT RAISE(ABORT, 'spend ledger is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS spend_ledger_no_delete BEFORE DELETE ON "
    "spend_ledger BEGIN SELECT RAISE(ABORT, 'spend ledger is append-only'); END",
)
_ensured: set[str] = set()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def ensure_schema(session) -> None:
    """Idempotent table + append-only triggers on the caller's database."""
    bind = session.get_bind()
    key = str(getattr(bind, "url", id(bind)))
    if key in _ensured:
        return
    await session.run_sync(lambda s: SpendLedger.__table__.create(s.connection(), checkfirst=True))
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
                  track: bool = True) -> float:
    """Atomically check all caps and append a reserve row in the caller's transaction.

    Raises SpendCapExceeded (nothing written). The caller commits.
    """
    usd = _valid(estimate_usd)
    await ensure_schema(session)
    c = caps()
    res = await session.execute(text(
        "INSERT INTO spend_ledger (created_at, day, kind, provider, project_id, run_id, source, usd, credits, note) "
        "SELECT :now, :day, 'reserve', :prov, :pid, :run, :src, :usd, :cr, :note WHERE "
        "(:rcap <= 0 OR :run = '' OR COALESCE((SELECT SUM(usd) FROM spend_ledger WHERE run_id = :run), 0) + :usd <= :rcap) AND "
        "(:pcap <= 0 OR :pid IS NULL OR COALESCE((SELECT SUM(usd) FROM spend_ledger WHERE project_id = :pid), 0) + :usd <= :pcap) AND "
        "(:dcap <= 0 OR COALESCE((SELECT SUM(usd) FROM spend_ledger WHERE day = :day), 0) + :usd <= :dcap)"),
        {"now": datetime.utcnow(), "day": _today(), "prov": provider[:32], "pid": project_id,
         "run": run_id[:80], "src": source[:32], "usd": usd, "cr": float(credits), "note": note[:200],
         "rcap": c["run"], "pcap": c["project"], "dcap": c["daily"]})
    if res.rowcount != 1:
        raise await _refusal(session, project_id, run_id, usd, source)
    if track:
        _pending.set(Pending(usd, project_id, run_id, source, provider))
    return usd


async def settle(session, *, project_id: int | None, run_id: str, reserved_usd: float,
                 actual_usd: float | None, source: str, provider: str = "",
                 credits_delta: float = 0.0) -> None:
    """Append actual - reserved; unknown actual leaves the reserve (conservative)."""
    if actual_usd is None or actual_usd < 0 or math.isnan(actual_usd):
        return
    delta = float(actual_usd) - float(reserved_usd)
    if delta:
        await ensure_schema(session)
        session.add(SpendLedger(day=_today(), kind="settle", provider=provider[:32],
                                project_id=project_id, run_id=run_id[:80], source=source[:32],
                                usd=delta, credits=credits_delta))


async def record_actual(session, *, project_id: int | None, usd: float, provider: str,
                        source: str = "native_call", run_id: str = "", note: str = "",
                        credits: float = 0.0) -> None:
    """Record a call that was never reserved (native pipeline). Never refuses."""
    usd = _valid(usd)
    await ensure_schema(session)
    session.add(SpendLedger(day=_today(), kind="actual", provider=provider[:32], project_id=project_id,
                            run_id=run_id[:80], source=source[:32], usd=usd, credits=float(credits), note=note[:200]))


async def record_event(session, *, project_id: int | None, provider: str, source: str, note: str = "") -> None:
    """Zero-USD audit row (kind event)."""
    await ensure_schema(session)
    session.add(SpendLedger(day=_today(), kind="event", provider=provider[:32], project_id=project_id,
                            source=source[:32], note=note[:200]))


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
    if session.get_bind().dialect.name == "sqlite":
        for ddl in _TASK_TRIGGERS:
            await session.execute(text(ddl))
    _task_ensured.add(key)


async def append_task_event(session, *, project_id: int | None, key: str, task_id: str, provider: str,
                            api: str, model: str, event: str, status: str,
                            est_credits: float | None = None, est_usd: float | None = None,
                            note: str = "") -> None:
    """Append one event; the caller commits. Never updates an existing row."""
    await ensure_task_schema(session)
    session.add(ProviderTaskLedger(project_id=project_id, key=key[:120], task_id=str(task_id)[:100],
                                   provider=provider[:32], api=api[:16], model=model[:64], event=event[:16],
                                   status=status[:16], est_credits=est_credits, est_usd=est_usd, note=note[:200]))


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


_tasks: set = set()


def schedule_actual(*, project_id: int | None, usd: float, provider: str) -> None:
    """Sync entry for _track_api_cost: settle the pending reservation or record a native call.

    Fire and forget; never raises and never blocks the caller.
    """
    try:
        pending = _pending.get()
        _pending.set(None)
        loop = asyncio.get_running_loop()
        task = loop.create_task(_write_actual(pending, project_id, float(usd), provider))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:  # noqa: BLE001 - recording must never affect the real request
        pass


async def _write_actual(pending, project_id, usd, provider) -> None:
    try:
        from app.db import SessionLocal
        async with SessionLocal() as s:
            if pending is not None:
                await settle(s, project_id=pending.project_id, run_id=pending.run_id,
                             reserved_usd=pending.usd, actual_usd=usd, source=pending.source,
                             provider=provider or pending.provider)
            else:
                await record_actual(s, project_id=project_id, usd=usd, provider=provider)
            await s.commit()
    except Exception:  # noqa: BLE001
        pass


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
        return False
