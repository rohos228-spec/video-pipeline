"""Журнал публикаций: идемпотентность, намерение до клика, lease на проект x платформа (A14).

Таблица `publications` создаётся здесь же (`CREATE TABLE IF NOT EXISTS`); регистрация в
`schema_migrations` делается отдельно (волна W2). Ключ идемпотентности: (project_id, platform, sha256).

Порядок для одной платформы (каждый шаг фиксируется commit-ом сразу, не ждёт конца шага):
  1. claim()         — запись НАМЕРЕНИЯ (status=planned) и lease; второй воркер получает busy;
  2. mark_clicked()  — status=clicked ДО реального клика; упали между кликом и записью итога
                       -> в журнале остаётся clicked, автоповтора нет (ждёт человека);
  3. record_result() — итог: confirmed / unconfirmed / failed / rejected.
Автоповтор разрешён ТОЛЬКО при сбое до клика (failed без clicked_at, planned с истёкшим lease,
skipped) и не более MAX_PRE_CLICK_ATTEMPTS раз.

Живой платформой не проверялось: только юнит и сухой прогон на SQLite.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LEASE_SECONDS = 900.0
MAX_PRE_CLICK_ATTEMPTS = 3

S_PLANNED = "planned"
S_CLICKED = "clicked"
S_CONFIRMED = "confirmed"
S_UNCONFIRMED = "unconfirmed"
S_FAILED = "failed"
S_REJECTED = "rejected"
S_SKIPPED = "skipped"
ALL_STATUSES = (S_PLANNED, S_CLICKED, S_CONFIRMED, S_UNCONFIRMED, S_FAILED, S_REJECTED, S_SKIPPED)

# claim-исходы
ACQUIRED = "acquired"
BUSY = "busy"  # другой воркер держит живой lease
DONE = "done"  # уже confirmed
NEEDS_HUMAN = "needs_human"  # клик мог состояться или попытки исчерпаны: автоповтор запрещён

_DDL = (
    """CREATE TABLE IF NOT EXISTS publications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        artifact_uuid TEXT,
        sha256 TEXT NOT NULL,
        account_id TEXT,
        status TEXT NOT NULL,
        post_url TEXT,
        post_id TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        screenshot TEXT,
        error_code TEXT,
        lease_owner TEXT,
        lease_until REAL,
        clicked_at REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_publications_idem ON publications(project_id, platform, sha256)",
    "CREATE INDEX IF NOT EXISTS ix_publications_project ON publications(project_id)",
)

_COLS = ("id, project_id, platform, artifact_uuid, sha256, account_id, status, post_url, post_id, "
         "attempts, screenshot, error_code, lease_owner, lease_until, clicked_at")

_schema_ready_for: set[int] = set()


@dataclass
class Claim:
    outcome: str
    pub_id: int | None = None
    owner: str = ""
    status: str = ""
    attempts: int = 0


def new_owner() -> str:
    return uuid.uuid4().hex


def idempotency_key(project_id: int, platform: str, sha256: str) -> str:
    return f"{project_id}:{platform}:{sha256}"


def sha256_of_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def sha256_of_file_async(path: str | Path) -> str:
    return await asyncio.to_thread(sha256_of_file, path)


async def ensure_schema(session: AsyncSession) -> None:
    key = id(session.get_bind()) if session.bind is not None else 0
    if key in _schema_ready_for:
        # В памяти (:memory:) таблица живёт с соединением, кэш по bind безопасен: bind тот же.
        try:
            await session.execute(text("SELECT 1 FROM publications LIMIT 0"))
            return
        except Exception:  # noqa: BLE001
            _schema_ready_for.discard(key)
    for ddl in _DDL:
        await session.execute(text(ddl))
    await session.commit()
    _schema_ready_for.add(key)


async def _commit(session: AsyncSession) -> None:
    try:
        from app.db import commit_with_retry
        await commit_with_retry(session)
    except ImportError:  # pragma: no cover
        await session.commit()


async def _get(session: AsyncSession, project_id: int, platform: str, sha256: str) -> dict | None:
    row = (await session.execute(
        text(f"SELECT {_COLS} FROM publications WHERE project_id=:p AND platform=:pl AND sha256=:s"),
        {"p": project_id, "pl": platform, "s": sha256})).mappings().first()
    return dict(row) if row else None


def is_auto_retryable(row: dict, now: float) -> bool:
    """Можно ли автоматически взять платформу снова (клик гарантированно не был сделан)."""
    st = row["status"]
    if row.get("clicked_at"):
        return False
    if st == S_SKIPPED:
        return True
    if st == S_FAILED:
        return int(row.get("attempts") or 0) < MAX_PRE_CLICK_ATTEMPTS
    if st == S_PLANNED:
        return (row.get("lease_until") or 0) < now
    return False


async def claim(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                artifact_uuid: str | None, owner: str, account_id: str | None = None,
                lease_s: float = LEASE_SECONDS, now: float | None = None) -> Claim:
    """Записать намерение публикации и взять lease. Атомарно через UNIQUE + условный UPDATE."""
    await ensure_schema(session)
    now = time.time() if now is None else now
    ins = await session.execute(text(
        "INSERT OR IGNORE INTO publications (project_id, platform, artifact_uuid, sha256, account_id, "
        "status, attempts, lease_owner, lease_until, created_at, updated_at) "
        "VALUES (:p, :pl, :au, :s, :acc, 'planned', 1, :o, :lu, :now, :now)"),
        {"p": project_id, "pl": platform, "au": artifact_uuid, "s": sha256, "acc": account_id,
         "o": owner, "lu": now + lease_s, "now": now})
    if ins.rowcount == 1:
        await _commit(session)
        row = await _get(session, project_id, platform, sha256)
        return Claim(ACQUIRED, row["id"], owner, S_PLANNED, 1)

    row = await _get(session, project_id, platform, sha256)
    await _commit(session)  # закрыть читающую транзакцию
    if row is None:  # pragma: no cover
        return Claim(BUSY)
    if row["status"] == S_CONFIRMED:
        return Claim(DONE, row["id"], status=row["status"], attempts=row["attempts"])
    if not is_auto_retryable(row, now):
        if row["status"] == S_PLANNED and not row.get("clicked_at"):
            return Claim(BUSY, row["id"], status=row["status"], attempts=row["attempts"])
        return Claim(NEEDS_HUMAN, row["id"], status=row["status"], attempts=row["attempts"])

    # условный UPDATE: выигрывает ровно один из параллельных воркеров
    upd = await session.execute(text(
        "UPDATE publications SET status='planned', attempts=attempts+1, lease_owner=:o, lease_until=:lu, "
        "error_code=NULL, updated_at=:now "
        "WHERE id=:id AND clicked_at IS NULL AND ("
        "  status='skipped' OR (status='failed' AND attempts<:mx) OR (status='planned' AND lease_until<:now))"),
        {"o": owner, "lu": now + lease_s, "now": now, "id": row["id"], "mx": MAX_PRE_CLICK_ATTEMPTS})
    await _commit(session)
    if upd.rowcount == 1:
        return Claim(ACQUIRED, row["id"], owner, S_PLANNED, int(row["attempts"]) + 1)
    return Claim(BUSY, row["id"], status=row["status"], attempts=row["attempts"])


async def mark_clicked(session: AsyncSession, pub_id: int, owner: str, *, now: float | None = None) -> bool:
    """Зафиксировать «сейчас кликнем» ДО клика. False — клик делать нельзя (lease потерян)."""
    now = time.time() if now is None else now
    upd = await session.execute(text(
        "UPDATE publications SET status='clicked', clicked_at=:now, updated_at=:now "
        "WHERE id=:id AND lease_owner=:o AND status='planned'"),
        {"now": now, "id": pub_id, "o": owner})
    await _commit(session)
    return upd.rowcount == 1


async def record_result(session: AsyncSession, pub_id: int, owner: str, *, status: str,
                        post_url: str | None = None, post_id: str | None = None,
                        error_code: str | None = None, screenshot: str | None = None,
                        now: float | None = None) -> str:
    """Записать итог платформы сразу. Возвращает фактически записанный статус.

    confirmed без post_url и без post_id невозможен: понижается до unconfirmed.
    """
    if status not in ALL_STATUSES:
        raise ValueError(f"unknown publication status {status!r}")
    now = time.time() if now is None else now
    if status == S_CONFIRMED and not (post_url or post_id):
        status, error_code = S_UNCONFIRMED, error_code or "confirmed_without_url"
    await session.execute(text(
        "UPDATE publications SET status=:st, post_url=:u, post_id=:pid, error_code=:e, screenshot=:sc, "
        "lease_owner=NULL, lease_until=NULL, updated_at=:now WHERE id=:id AND lease_owner=:o"),
        {"st": status, "u": post_url, "pid": post_id, "e": error_code, "sc": screenshot,
         "now": now, "id": pub_id, "o": owner})
    await _commit(session)
    row = (await session.execute(text("SELECT status FROM publications WHERE id=:id"),
                                 {"id": pub_id})).first()
    await _commit(session)
    return row[0] if row else status


async def record_unclaimed(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                           artifact_uuid: str | None, status: str, clicked: bool,
                           post_url: str | None = None, post_id: str | None = None,
                           error_code: str | None = None, now: float | None = None) -> str:
    """Записать итог, пришедший без claim (например из старого вызова publish_everywhere).

    Не затирает confirmed. Если клик был, ставим clicked_at, чтобы автоповтора не было.
    """
    await ensure_schema(session)
    now = time.time() if now is None else now
    if status == S_CONFIRMED and not (post_url or post_id):
        status, error_code = S_UNCONFIRMED, error_code or "confirmed_without_url"
    await session.execute(text(
        "INSERT OR IGNORE INTO publications (project_id, platform, artifact_uuid, sha256, status, attempts, "
        "clicked_at, created_at, updated_at) VALUES (:p, :pl, :au, :s, 'planned', 1, :ca, :now, :now)"),
        {"p": project_id, "pl": platform, "au": artifact_uuid, "s": sha256,
         "ca": now if clicked else None, "now": now})
    await session.execute(text(
        "UPDATE publications SET status=:st, post_url=:u, post_id=:pid, error_code=:e, "
        "clicked_at=CASE WHEN :cl=1 AND clicked_at IS NULL THEN :now ELSE clicked_at END, "
        "lease_owner=NULL, lease_until=NULL, updated_at=:now "
        "WHERE project_id=:p AND platform=:pl AND sha256=:s AND status<>'confirmed'"),
        {"st": status, "u": post_url, "pid": post_id, "e": error_code, "cl": 1 if clicked else 0,
         "now": now, "p": project_id, "pl": platform, "s": sha256})
    await _commit(session)
    row = await _get(session, project_id, platform, sha256)
    await _commit(session)
    return row["status"] if row else status


async def mark_skipped(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                       artifact_uuid: str | None, reason: str, now: float | None = None) -> None:
    """Публикация выключена флагом: пишем skipped (не published). Подтверждённое не трогаем."""
    await ensure_schema(session)
    now = time.time() if now is None else now
    await session.execute(text(
        "INSERT OR IGNORE INTO publications (project_id, platform, artifact_uuid, sha256, status, attempts, "
        "error_code, created_at, updated_at) VALUES (:p, :pl, :au, :s, 'skipped', 0, :e, :now, :now)"),
        {"p": project_id, "pl": platform, "au": artifact_uuid, "s": sha256, "e": reason, "now": now})
    await session.execute(text(
        "UPDATE publications SET status='skipped', error_code=:e, updated_at=:now "
        "WHERE project_id=:p AND platform=:pl AND sha256=:s AND status='planned' AND clicked_at IS NULL "
        "AND (lease_until IS NULL OR lease_until<:now)"),
        {"e": reason, "now": now, "p": project_id, "pl": platform, "s": sha256})
    await _commit(session)


async def list_for(session: AsyncSession, project_id: int, sha256: str | None = None) -> list[dict]:
    await ensure_schema(session)
    sql = f"SELECT {_COLS} FROM publications WHERE project_id=:p"
    params: dict = {"p": project_id}
    if sha256 is not None:
        sql += " AND sha256=:s"
        params["s"] = sha256
    rows = (await session.execute(text(sql + " ORDER BY id"), params)).mappings().all()
    await _commit(session)
    return [dict(r) for r in rows]


def summarize(rows: list[dict], platforms: list[str], now: float | None = None) -> dict:
    """Итог по проекту: published разрешён только если все платформы confirmed с URL/ID."""
    now = time.time() if now is None else now
    by = {r["platform"]: r for r in rows}
    confirmed = [p for p in platforms
                 if p in by and by[p]["status"] == S_CONFIRMED and (by[p]["post_url"] or by[p]["post_id"])]
    retryable, busy, human, skipped = [], [], [], []
    for p in platforms:
        r = by.get(p)
        if r is None:
            retryable.append(p)
        elif p in confirmed:
            continue
        elif r["status"] == S_PLANNED and not r.get("clicked_at") and (r.get("lease_until") or 0) >= now:
            busy.append(p)
        elif r["status"] == S_SKIPPED:
            skipped.append(p)
        elif is_auto_retryable(r, now):
            retryable.append(p)
        else:
            human.append(p)
    return {"all_confirmed": len(confirmed) == len(platforms), "confirmed": confirmed,
            "retryable": retryable, "busy": busy, "needs_human": human, "skipped": skipped}
