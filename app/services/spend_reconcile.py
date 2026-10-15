"""B10: сверка денег. Суммы журналов (``spend_ledger`` + ``tracker.db``) против снимков баланса провайдера
до и после платного шага.

Сеть здесь не ходит. Баланс приносит адаптер-колбэк, который подключает вызывающий код
(``register_balance_adapter``): для Kie это запрос кредитов, для Outsee и ElevenLabs это число,
которое владелец вводит из кабинета. Без адаптера и без переданного снимка сверка честно отвечает
«нет снимка баланса» и не изображает сходимость.

Результат сверки пишется:
* в ``spend_ledger`` строкой ``kind=event`` (0 USD): ``source=reconcile_ok`` или ``reconcile_gap``,
  числа лежат в ``note`` (журнал append-only, строку не изменить);
* при несходимости ещё и строкой инцидента ``observation_incidents`` типа ``spend_mismatch``.

Отчёт по проекту (``project_report``) читает ``telemetry.project_telemetry``.
"""
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

DEFAULT_TOLERANCE = 0.05          # цель владельца: 5%; задаётся параметром ``tolerance``
SOURCE_OK = "reconcile_ok"
SOURCE_GAP = "reconcile_gap"
INCIDENT_TYPE = "spend_mismatch"
INCIDENT_EXECUTION = "reconcile"  # у сверки нет запуска; поле обязательное в таблице инцидентов

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

# Адаптер возвращает баланс провайдера в USD (число или awaitable числа).
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
    source: str = "adapter"     # adapter | manual | error
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.balance_usd is not None


def manual_snapshot(provider: str, balance_usd: float, *, taken_at: datetime | None = None) -> Snapshot:
    """Снимок из кабинета, который вводит владелец."""
    return Snapshot(_norm(provider), float(balance_usd), taken_at or datetime.utcnow(), "manual")


async def take_snapshot(provider: str, adapter: BalanceAdapter | None = None) -> Snapshot:
    """Снимок через адаптер. Нет адаптера или он упал: снимок без числа и с причиной (не исключение)."""
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
    except Exception as exc:  # noqa: BLE001 - любой отказ колбэка становится видимой причиной
        return Snapshot(key, None, now, "error", f"{type(exc).__name__}: {exc}"[:200])


# --------------------------------------------------------------------------- чистое сравнение


def compare(*, provider: str, before: Snapshot | None, after: Snapshot | None,
            ledger_usd: float, tracker_usd: float = 0.0, overlap_usd: float = 0.0,
            tolerance: float = DEFAULT_TOLERANCE, abs_tolerance_usd: float = 0.0,
            foreign_usd: float = 0.0, boundary_usd: float = 0.0) -> dict[str, Any]:
    """Сравнивает убыль баланса с суммой журналов.

    ``journal_usd = ledger_usd + tracker_usd - overlap_usd``: журналы покрывают разные швы (платные вызовы
    медиа против LLM-вызовов трекера); если вызывающий знает, что одну трату записали оба, он вычитает
    её в ``overlap_usd``. Допуск относительный (``tolerance`` от большего из двух чисел) и/или абсолютный.

    W70/K11: баланс провайдера общий. ``foreign_usd`` - траты этого провайдера в окне вне сверяемого проекта,
    ``boundary_usd`` - траты проекта, записанные между отметкой окна и снимком (или после снимка): их нет в
    убыли баланса или они в неё не попали. Если без них сумма не сходится, это не доказанное расхождение, а
    ``inconclusive`` (без инцидента). Сошедшаяся сверка остаётся сошедшейся.
    """
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
    spent = float(before.balance_usd) - float(after.balance_usd)  # type: ignore[arg-type]
    gap = spent - journal               # > 0: провайдер списал больше, чем записано
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
    """Одна строка для оператора."""
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


# --------------------------------------------------------------------------- чтение журналов


@dataclass
class Mark:
    """Положение журналов перед шагом: сумму окна считаем по id строк, а не по часам."""
    ledger_max_id: int
    tracker_max_id: int


def _tracker_path():
    from app.services import api_tracker_hook
    return api_tracker_hook._TRACKER_DB


def _tracker_sync(project_id: int | None, provider: str | None, after_id: int,
                  db_path=None) -> tuple[float, int]:
    """(сумма cost_usd строк с id > after_id, максимальный id). Нет файла или таблицы: (0, 0)."""
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
    # after_id огромный: сумма пустая, нужен только максимальный id
    return Mark(await _ledger_max_id(session), tracker_max)


async def window_sums(session, start: Mark, *, provider: str, project_id: int | None = None,
                      tracker_db=None,
                      ambiguous: "list[tuple[int, int | None]] | None" = None) -> dict[str, float]:
    """Суммы журналов за окно после ``start``. Сервисные строки сверки (kind=event, 0 USD) сумму не меняют.

    Кроме ``ledger_usd`` / ``tracker_usd`` (строки проекта) отдаёт ``foreign_usd`` (строки этого провайдера в окне
    у других проектов или без проекта) и ``boundary_usd`` (строки проекта с id внутри ``ambiguous`` - пар
    ``(после_id, до_id включительно | None)``: записаны у границ снимков). Это для ``compare``: баланс общий."""
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


# --------------------------------------------------------------------------- запись результата


def _note(result: dict[str, Any]) -> str:
    keep = {k: result.get(k) for k in ("provider_spent_usd", "journal_usd", "ledger_usd", "tracker_usd",
                                       "gap_usd", "gap_ratio", "tolerance", "status", "foreign_usd", "boundary_usd")}
    return json.dumps(keep, ensure_ascii=False, separators=(",", ":"))[:200]


async def record_result(session, result: dict[str, Any], *, project_id: int | None,
                        window_key: str = "") -> str | None:
    """Пишет строку журнала (всегда) и инцидент (только при расхождении). Коммит делает вызывающий.
    Возвращает id инцидента или None."""
    from app.services import spend_guard
    ok = result.get("status") == STATUS_CONVERGED
    inconclusive = result.get("status") == STATUS_INCONCLUSIVE
    await spend_guard.record_event(session, project_id=project_id, provider=result["provider"],
                                   source=SOURCE_OK if ok else (SOURCE_INCONCLUSIVE if inconclusive else SOURCE_GAP),
                                   note=_note(result))
    if ok or inconclusive:  # inconclusive: no proof of a mismatch, so no incident
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
        return incident_id            # повторная запись того же окна не плодит дубли
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


# --------------------------------------------------------------------------- шаг под сверкой


@asynccontextmanager
async def reconcile_window(session_factory, provider: str, *, project_id: int | None = None,
                           tolerance: float = DEFAULT_TOLERANCE, abs_tolerance_usd: float = 0.0,
                           overlap_usd: float = 0.0, adapter: BalanceAdapter | None = None,
                           before: Snapshot | None = None, after_snapshot: Callable[[], Snapshot] | None = None,
                           tracker_db=None):
    """Оборачивает платный шаг: снимок до, шаг, снимок после, сверка и запись.

    ``session_factory`` даёт сессию (async_sessionmaker). Итог кладётся в словарь, который отдаёт ``as``:
    ``async with reconcile_window(...) as out: ...; out["result"]``. Исключение шага сверку не отменяет:
    деньги могли уйти и при ошибке; исключение после сверки пробрасывается дальше.
    """
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
    """Сверка по уже готовым снимкам (владелец ввёл баланс из кабинета до и после)."""
    sums = await window_sums(session, start, provider=provider, project_id=project_id, tracker_db=tracker_db)
    result = compare(provider=provider, before=before, after=after, overlap_usd=overlap_usd,
                     tolerance=tolerance, abs_tolerance_usd=abs_tolerance_usd, **sums)
    result["incident_id"] = await record_result(session, result, project_id=project_id,
                                                window_key=f"{start.ledger_max_id}-{start.tracker_max_id}")
    if commit:
        await session.commit()
    return result


# --------------------------------------------------------------------------- отчёт по проекту


async def project_report(session, project_id: int, *, since: datetime | None = None,
                         tolerance: float = DEFAULT_TOLERANCE, tracker_db=None,
                         recent: int = 10) -> dict[str, Any]:
    """Оценено / фактически / расхождение / открытые резервы по проекту. Только чтение.

    * ``estimated_usd``: сумма резервов (``reserve``) и строк ``estimated``;
    * ``actual_usd``: сумма фактов (``actual``) плюс поправки ``settle`` (факт минус резерв);
    * ``ledger_total_usd``: сумма всех строк (то, что считают потолки);
    * ``open_reserves``: резервы по запускам, где нет строки ``settle``. Точное совпадение резерва с фактом
      строки ``settle`` не оставляет, поэтому это верхняя граница, а не точное число;
    * ``reconciliations``: последние результаты сверки с балансом (строки ``reconcile_*``).
    """
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
