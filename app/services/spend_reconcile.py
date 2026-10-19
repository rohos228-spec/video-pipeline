from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

DEFAULT_TOLERANCE = 0.05
SOURCE_OK = "reconcile_ok"
SOURCE_GAP = "reconcile_gap"
INCIDENT_TYPE = "spend_mismatch"
INCIDENT_EXECUTION = "reconcile"

STATUS_CONVERGED = "converged"
STATUS_GAP = "gap"
STATUS_NO_SNAPSHOT = "no_snapshot"
STATUS_INCONCLUSIVE = "inconclusive"
SOURCE_INCONCLUSIVE = "reconcile_inconclusive"

STATUS_RU = {
    STATUS_CONVERGED: "сходится",
    STATUS_GAP: "расхождение",
    STATUS_NO_SNAPSHOT: "нет снимка баланса",
    STATUS_INCONCLUSIVE: "не удалось сверить",
}

BalanceAdapter = Callable[[], "float | Awaitable[float]"]
_adapters: dict[str, BalanceAdapter] = {}


def register_balance_adapter(provider: str, adapter: BalanceAdapter | None) -> None:
    key = _norm(provider)
    if adapter is None:
        _adapters.pop(key, None)
    else:
        _adapters[key] = adapter


def _norm(provider: str | None) -> str:
    raw = str(provider or "").strip().lower()
    return "kie" if raw in {"kie", "kie.ai"} else (raw or "unknown")


@dataclass
class Snapshot:
    provider: str
    balance_usd: float | None
    taken_at: datetime
    source: str = "adapter"
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.balance_usd is not None


def manual_snapshot(provider: str, balance_usd: float, *, taken_at: datetime | None = None) -> Snapshot:
    return Snapshot(_norm(provider), float(balance_usd), taken_at or datetime.utcnow(), "manual")


async def take_snapshot(provider: str, adapter: BalanceAdapter | None = None) -> Snapshot:
    key = _norm(provider)
    fn = adapter or _adapters.get(key)
    now = datetime.utcnow()
    if fn is None:
        return Snapshot(key, None, now, "error", "адаптер баланса не подключён")
    try:
        value = fn()
        if inspect.isawaitable(value):
            value = await value
        value = float(value)
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("баланс не число")
        return Snapshot(key, value, now, "adapter")
    except Exception as exc:
        return Snapshot(key, None, now, "error", f"{type(exc).__name__}: {exc}"[:200])




def compare(*, provider: str, before: Snapshot | None, after: Snapshot | None,
            ledger_usd: float, tracker_usd: float = 0.0, overlap_usd: float = 0.0,
            tolerance: float = DEFAULT_TOLERANCE, abs_tolerance_usd: float = 0.0,
            foreign_usd: float = 0.0, boundary_usd: float = 0.0) -> dict[str, Any]:
    if tolerance < 0 or abs_tolerance_usd < 0:
        raise ValueError("допуск не может быть отрицательным")
    journal = float(ledger_usd) + float(tracker_usd) - float(overlap_usd)
    out: dict[str, Any] = {
        "provider": _norm(provider), "ledger_usd": round(float(ledger_usd), 6),
        "tracker_usd": round(float(tracker_usd), 6), "overlap_usd": round(float(overlap_usd), 6),
        "journal_usd": round(journal, 6), "tolerance": tolerance, "abs_tolerance_usd": abs_tolerance_usd,
        "balance_before": before.balance_usd if before else None,
        "balance_after": after.balance_usd if after else None,
        "provider_spent_usd": None, "gap_usd": None, "gap_ratio": None,
        "foreign_usd": round(float(foreign_usd), 6), "boundary_usd": round(float(boundary_usd), 6),
    }
    if before is None or after is None or not before.ok or not after.ok:
        reasons = [s.error for s in (before, after) if s is not None and not s.ok and s.error]
        out.update(status=STATUS_NO_SNAPSHOT, status_ru=STATUS_RU[STATUS_NO_SNAPSHOT], converged=False,
                   reason_ru="; ".join(reasons) or "снимок баланса до или после шага не получен")
        return out
    spent = float(before.balance_usd) - float(after.balance_usd)
    gap = spent - journal
    base = max(abs(spent), abs(journal))
    ratio = (abs(gap) / base) if base > 0 else 0.0
    converged = abs(gap) <= abs_tolerance_usd + 1e-9 or ratio <= tolerance + 1e-12
    out.update(provider_spent_usd=round(spent, 6), gap_usd=round(gap, 6), gap_ratio=round(ratio, 6),
               status=STATUS_CONVERGED if converged else STATUS_GAP,
               status_ru=STATUS_RU[STATUS_CONVERGED if converged else STATUS_GAP], converged=converged)
    if not converged and (float(foreign_usd) > 1e-9 or float(boundary_usd) > 1e-9):
        out.update(status=STATUS_INCONCLUSIVE, status_ru=STATUS_RU[STATUS_INCONCLUSIVE],
                   reason_ru=("баланс общий: в окне были траты провайдера вне этого проекта или у границ снимков "
                              f"(вне проекта ${float(foreign_usd):.4f}, у границ ${float(boundary_usd):.4f}), "
                              "расхождение не доказано"))
    elif not converged:
        if spent < 0:
            out["reason_ru"] = "баланс провайдера вырос: пополнение или возврат внутри окна, сверка невозможна"
        elif gap > 0:
            out["reason_ru"] = "провайдер списал больше, чем записано в журналах: есть траты без записи"
        else:
            out["reason_ru"] = "в журналах записано больше, чем списал провайдер: оценки завышены или резерв не снят"
    return out


def message_ru(result: dict[str, Any]) -> str:
    p = result.get("provider", "?")
    if result.get("status") == STATUS_NO_SNAPSHOT:
        return f"Сверка {p}: нет снимка баланса ({result.get('reason_ru', '')}). Сходимость не проверена."
    if result.get("status") == STATUS_INCONCLUSIVE:
        return (f"Сверка {p}: не удалось сверить, расхождение не доказано "
                f"({result.get('reason_ru', '')}).")
    base = (f"Сверка {p}: провайдер списал ${result['provider_spent_usd']:.4f}, "
            f"в журналах ${result['journal_usd']:.4f}, разница ${result['gap_usd']:+.4f} "
            f"({result['gap_ratio'] * 100:.1f}% при допуске {result['tolerance'] * 100:.1f}%)")
    if result["converged"]:
        return base + ": сходится."
    return base + f": расхождение. {result.get('reason_ru', '')}"




@dataclass
class Mark:
    ledger_max_id: int
    tracker_max_id: int


def _tracker_path():
    from app.services import api_tracker_hook
    return api_tracker_hook._TRACKER_DB


def _tracker_sync(project_id: int | None, provider: str | None, after_id: int,
                  db_path=None) -> tuple[float, int]:
    path = db_path or _tracker_path()
    if not path.is_file():
        return 0.0, 0
    sql = "SELECT id, cost_usd, provider FROM api_calls WHERE id > ?"
    params: list[Any] = [after_id]
    if project_id is not None:
        sql += " AND project_source = ?"
        params.append(str(project_id))
    try:
        with closing(sqlite3.connect(str(path), timeout=5.0)) as conn:
            rows = conn.execute(sql, params).fetchall()
            max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM api_calls").fetchone()[0]
    except sqlite3.OperationalError:
        return 0.0, 0
    want = _norm(provider) if provider else None
    total = sum(float(r[1] or 0.0) for r in rows if want is None or _norm(r[2]) == want)
    return total, int(max_id or 0)


async def _ledger_max_id(session) -> int:
    try:
        return int((await session.execute(text("SELECT COALESCE(MAX(id), 0) FROM spend_ledger"))).scalar() or 0)
    except (OperationalError, ProgrammingError):
        await session.rollback()
        return 0


async def mark(session, *, tracker_db=None) -> Mark:
    _, tracker_max = await asyncio.to_thread(_tracker_sync, None, None, 2 ** 62, tracker_db)
    return Mark(await _ledger_max_id(session), tracker_max)


async def window_sums(session, start: Mark, *, provider: str, project_id: int | None = None,
                      tracker_db=None,
                      ambiguous: "list[tuple[int, int | None]] | None" = None) -> dict[str, float]:
    sql = "SELECT id, usd, provider, project_id FROM spend_ledger WHERE id > :a"
    params: dict[str, Any] = {"a": start.ledger_max_id}
    ledger = foreign = boundary = 0.0
    try:
        for rid, usd, prov, pid in (await session.execute(text(sql), params)).all():
            if _norm(prov) != _norm(provider):
                continue
            usd = float(usd or 0.0)
            if project_id is not None and pid != project_id:
                foreign += abs(usd)
                continue
            ledger += usd
            if any(rid > lo and (hi is None or rid <= hi) for lo, hi in (ambiguous or ())):
                boundary += abs(usd)
    except (OperationalError, ProgrammingError):
        await session.rollback()
    tracker, _ = await asyncio.to_thread(_tracker_sync, project_id, provider, start.tracker_max_id, tracker_db)
    return {"ledger_usd": ledger, "tracker_usd": tracker, "foreign_usd": foreign, "boundary_usd": boundary}




def _note(result: dict[str, Any]) -> str:
    keep = {k: result.get(k) for k in ("provider_spent_usd", "journal_usd", "ledger_usd", "tracker_usd",
                                       "gap_usd", "gap_ratio", "tolerance", "status", "foreign_usd", "boundary_usd")}
    return json.dumps(keep, ensure_ascii=False, separators=(",", ":"))[:200]


async def record_result(session, result: dict[str, Any], *, project_id: int | None,
                        window_key: str = "") -> str | None:
    from app.services import spend_guard
    ok = result.get("status") == STATUS_CONVERGED
    inconclusive = result.get("status") == STATUS_INCONCLUSIVE
    await spend_guard.record_event(session, project_id=project_id, provider=result["provider"],
                                   source=SOURCE_OK if ok else (SOURCE_INCONCLUSIVE if inconclusive else SOURCE_GAP),
                                   note=_note(result))
    if ok or inconclusive:
        return None
    return await _open_incident(session, result, project_id, window_key)


async def _open_incident(session, result: dict[str, Any], project_id: int | None, window_key: str) -> str:
    from app.services.diagnosis import Incident
    await session.run_sync(lambda s: Incident.__table__.create(s.connection(), checkfirst=True))
    unknown = result.get("status") == STATUS_NO_SNAPSHOT
    incident_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                 f"{INCIDENT_EXECUTION}|{INCIDENT_TYPE}|{result['provider']}|{project_id}|{window_key}"))
    existing = await session.get(Incident, incident_id)
    if existing is not None:
        return incident_id
    fact = message_ru(result)
    session.add(Incident(
        incident_id=incident_id, execution_id=INCIDENT_EXECUTION, project_id=int(project_id or 0),
        node_key=None, attempt_id=None, type=INCIDENT_TYPE,
        severity="warning" if unknown else "error", status="open",
        details={
            "type": INCIDENT_TYPE, "title": "Деньги не сходятся с балансом провайдера",
            "fact": fact, "blocking": False, "currently_relevant": True,
            "cause": {"hypothesis": None, "certainty": "unknown"},
            "provider": result["provider"], "reconcile": result,
            "recommendation": {"action": "check_spend",
                               "title": "Сверить кабинет провайдера с журналом трат",
                               "reason": fact, "automatic_action": False},
        }))
    return incident_id




@asynccontextmanager
async def reconcile_window(session_factory, provider: str, *, project_id: int | None = None,
                           tolerance: float = DEFAULT_TOLERANCE, abs_tolerance_usd: float = 0.0,
                           overlap_usd: float = 0.0, adapter: BalanceAdapter | None = None,
                           before: Snapshot | None = None, after_snapshot: Callable[[], Snapshot] | None = None,
                           tracker_db=None):
    out: dict[str, Any] = {}
    async with session_factory() as session:
        start = await mark(session, tracker_db=tracker_db)
    snap_before = before or await take_snapshot(provider, adapter)
    try:
        yield out
    finally:
        snap_after = after_snapshot() if after_snapshot else await take_snapshot(provider, adapter)
        async with session_factory() as session:
            sums = await window_sums(session, start, provider=provider, project_id=project_id,
                                     tracker_db=tracker_db)
            result = compare(provider=provider, before=snap_before, after=snap_after,
                             overlap_usd=overlap_usd, tolerance=tolerance,
                             abs_tolerance_usd=abs_tolerance_usd, **sums)
            key = f"{start.ledger_max_id}-{start.tracker_max_id}"
            out["incident_id"] = await record_result(session, result, project_id=project_id, window_key=key)
            await session.commit()
        out["result"] = result


async def reconcile_now(session, *, provider: str, project_id: int | None, start: Mark,
                        before: Snapshot, after: Snapshot, tolerance: float = DEFAULT_TOLERANCE,
                        abs_tolerance_usd: float = 0.0, overlap_usd: float = 0.0,
                        tracker_db=None, commit: bool = True) -> dict[str, Any]:
    sums = await window_sums(session, start, provider=provider, project_id=project_id, tracker_db=tracker_db)
    result = compare(provider=provider, before=before, after=after, overlap_usd=overlap_usd,
                     tolerance=tolerance, abs_tolerance_usd=abs_tolerance_usd, **sums)
    result["incident_id"] = await record_result(session, result, project_id=project_id,
                                                window_key=f"{start.ledger_max_id}-{start.tracker_max_id}")
    if commit:
        await session.commit()
    return result




async def project_report(session, project_id: int, *, since: datetime | None = None,
                         tolerance: float = DEFAULT_TOLERANCE, tracker_db=None,
                         recent: int = 10) -> dict[str, Any]:
    where = "project_id = :p"
    params: dict[str, Any] = {"p": project_id}
    if since is not None:
        where += " AND created_at >= :s"
        params["s"] = since
    rows: list[Any] = []
    try:
        rows = (await session.execute(text(
            "SELECT id, kind, provider, run_id, source, usd, note, created_at FROM spend_ledger WHERE "
            + where + " ORDER BY id"), params)).all()
    except (OperationalError, ProgrammingError):
        await session.rollback()
    est = act = settle = total = 0.0
    by_kind: dict[str, float] = {}
    reserve_by_run: dict[str, float] = {}
    settled_runs: set[str] = set()
    by_provider: dict[str, dict[str, float]] = {}
    recs: list[dict[str, Any]] = []
    for rid, kind, prov, run_id, source, usd, note, created in rows:
        usd = float(usd or 0.0)
        total += usd
        by_kind[kind] = by_kind.get(kind, 0.0) + usd
        slot = by_provider.setdefault(_norm(prov), {"estimated_usd": 0.0, "actual_usd": 0.0, "total_usd": 0.0})
        slot["total_usd"] += usd
        if kind in ("reserve", "estimated"):
            est += usd
            slot["estimated_usd"] += usd
            if kind == "reserve":
                reserve_by_run[run_id or ""] = reserve_by_run.get(run_id or "", 0.0) + usd
        elif kind == "actual":
            act += usd
            slot["actual_usd"] += usd
        elif kind == "settle":
            settle += usd
            settled_runs.add(run_id or "")
        elif kind == "event" and source in (SOURCE_OK, SOURCE_GAP, SOURCE_INCONCLUSIVE):
            try:
                detail = json.loads(note or "{}")
            except ValueError:
                detail = {}
            recs.append({"id": rid, "provider": _norm(prov), "at": str(created), "source": source,
                         "converged": source == SOURCE_OK, **detail})
    open_by_run = {r: v for r, v in reserve_by_run.items() if r not in settled_runs and v > 0}
    tracker_total, _ = await asyncio.to_thread(_tracker_sync_total, project_id, since, tracker_db)
    gap = total - tracker_total
    base = max(abs(total), abs(tracker_total))
    ratio = (abs(gap) / base) if base > 0 else 0.0
    open_incidents = await _open_incident_count(session, project_id)
    return {
        "project_id": project_id,
        "since": since.isoformat() if since else None,
        "estimated_usd": round(est, 6),
        "actual_usd": round(act + settle, 6),
        "settle_adjustment_usd": round(settle, 6),
        "ledger_total_usd": round(total, 6),
        "tracker_total_usd": round(tracker_total, 6),
        "ledger_vs_tracker_gap_usd": round(gap, 6),
        "ledger_vs_tracker_ratio": round(ratio, 6),
        "ledger_vs_tracker_flag": ratio > tolerance and base > 0,
        "ledger_vs_tracker_note_ru": ("журналы покрывают разные швы (медиа и LLM), поэтому разница сама по себе "
                                      "не ошибка; доказательство даёт сверка с балансом провайдера"),
        "by_kind": {k: round(v, 6) for k, v in by_kind.items()},
        "by_provider": {k: {kk: round(vv, 6) for kk, vv in v.items()} for k, v in by_provider.items()},
        "open_reserves_usd": round(sum(open_by_run.values()), 6),
        "open_reserves_runs": len(open_by_run),
        "reconciliations": recs[-recent:],
        "reconciliations_total": len(recs),
        "reconciliations_gap": sum(1 for r in recs if not r["converged"] and r["source"] != SOURCE_INCONCLUSIVE),
        "reconciliations_inconclusive": sum(1 for r in recs if r["source"] == SOURCE_INCONCLUSIVE),
        "open_mismatch_incidents": open_incidents,
        "balance_reconciled": any(r["source"] != SOURCE_INCONCLUSIVE for r in recs),
    }


def _tracker_sync_total(project_id: int, since: datetime | None, db_path=None) -> tuple[float, int]:
    path = db_path or _tracker_path()
    if not path.is_file():
        return 0.0, 0
    sql = "SELECT COALESCE(SUM(cost_usd), 0.0), COUNT(*) FROM api_calls WHERE project_source = ?"
    params: list[Any] = [str(project_id)]
    if since is not None:
        sql += " AND timestamp >= ?"
        params.append(since.isoformat())
    try:
        with closing(sqlite3.connect(str(path), timeout=5.0)) as conn:
            total, count = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return 0.0, 0
    return float(total or 0.0), int(count or 0)


async def _open_incident_count(session, project_id: int) -> int:
    try:
        return int((await session.execute(text(
            "SELECT COUNT(*) FROM observation_incidents WHERE project_id = :p AND type = :t "
            "AND status != 'resolved_confirmed'"), {"p": project_id, "t": INCIDENT_TYPE})).scalar() or 0)
    except (OperationalError, ProgrammingError):
        await session.rollback()
        return 0
