"""SSE-шина оркестратора.

Монотонный ``seq`` на сессию. Token-события живут только в кольце на 1000
событий и в БД не пишутся. Номера для них берутся из памяти пачками
(``SEQ_RESERVE``): в ``orch_event_seq`` уходит верхняя отметка резерва,
не каждый токен. Укрупнённые события пишутся в ``orch_events`` через
одно долгоживущее соединение на файл БД (WAL). Компакция — отдельно.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.orchestrator.contracts import (
    EPHEMERAL_EVENT_TYPES,
    EVENT_RETENTION_DAYS,
    RETAINED_EVENT_TYPES,
    SESSION_EVENT_CAP,
    TOKEN_RING_SIZE,
    OrchEvent,
)
from app.orchestrator.db.migrations import connect, resolve_db_path

# Сколько seq резервировать одним UPDATE. Токен не трогает БД, пока пачка не кончилась.
SEQ_RESERVE = 64

_LOCK = threading.RLock()
_CONNS: dict[str, Any] = {}
_BATCHES: dict[tuple[str, str], list[int]] = {}
_RESERVES: dict[tuple[str, str], int] = {}

_KEEP_SQL = ", ".join(f"'{name}'" for name in sorted(RETAINED_EVENT_TYPES))


class EventBus:
    def __init__(self) -> None:
        self._rings: dict[str, deque[OrchEvent]] = {}
        self._subs: dict[str, set[asyncio.Queue[OrchEvent]]] = {}

    async def publish(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        task_id: int | None = None,
        created_at: str | None = None,
    ) -> OrchEvent:
        ephemeral = event_type in EPHEMERAL_EVENT_TYPES
        seq, stored_at = _insert_event(
            session_id,
            event_type,
            payload or {},
            task_id=task_id,
            created_at=created_at,
            persist=not ephemeral,
        )
        event = OrchEvent(
            type=event_type,
            task_id=task_id,
            payload=dict(payload or {}),
            seq=seq,
            session_id=session_id,
        )
        if ephemeral:
            ring = self._rings.setdefault(session_id, deque(maxlen=TOKEN_RING_SIZE))
            ring.append(event)
        for queue in list(self._subs.get(session_id, ())):
            queue.put_nowait(event)
        return event

    def replay(self, session_id: str, last_seq: int) -> list[OrchEvent]:
        """События с ``seq > last_seq``: БД (укрупнённые) + кольцо token."""
        events = _load_persisted(session_id, last_seq)
        for event in self._rings.get(session_id, ()):
            if event.seq > last_seq:
                events.append(event)
        events.sort(key=lambda item: item.seq)
        return events

    def subscribe(self, session_id: str) -> asyncio.Queue[OrchEvent]:
        queue: asyncio.Queue[OrchEvent] = asyncio.Queue()
        self._subs.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[OrchEvent]) -> None:
        subs = self._subs.get(session_id)
        if not subs:
            return
        subs.discard(queue)
        if not subs:
            self._subs.pop(session_id, None)

    @asynccontextmanager
    async def subscription(self, session_id: str) -> AsyncIterator[asyncio.Queue[OrchEvent]]:
        queue = self.subscribe(session_id)
        try:
            yield queue
        finally:
            self.unsubscribe(session_id, queue)


def compact_events(
    session_id: str | None = None,
    *,
    now: datetime | None = None,
    retention_days: int = EVENT_RETENTION_DAYS,
    cap: int = SESSION_EVENT_CAP,
) -> dict[str, int]:
    """Удалить события старше ``retention_days``, кроме checkpoint/approval.

    Затем жёсткий кап ``cap`` на сессию: сначала старые обычные события,
    если их не хватает — самые старые защищённые тоже.
    """
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cutoff = (moment - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn = connect()
    try:
        if session_id is None:
            sessions = [row[0] for row in conn.execute("SELECT DISTINCT session_id FROM orch_events")]
        else:
            sessions = [session_id]
        deleted_age = 0
        deleted_cap = 0
        for sid in sessions:
            cur = conn.execute(
                f"""
                DELETE FROM orch_events
                WHERE session_id = ?
                  AND created_at < ?
                  AND type NOT IN ({_KEEP_SQL})
                """,
                (sid, cutoff),
            )
            deleted_age += cur.rowcount if cur.rowcount is not None else 0
            deleted_cap += _enforce_cap(conn, sid, cap)
        conn.commit()
        return {"deleted_age": deleted_age, "deleted_cap": deleted_cap}
    finally:
        conn.close()


def _enforce_cap(conn: Any, session_id: str, cap: int) -> int:
    rows = conn.execute(
        "SELECT id, type FROM orch_events WHERE session_id = ? ORDER BY seq ASC, id ASC",
        (session_id,),
    ).fetchall()
    overflow = len(rows) - cap
    if overflow <= 0:
        return 0
    ordinary = [row["id"] for row in rows if row["type"] not in RETAINED_EVENT_TYPES]
    protected = [row["id"] for row in rows if row["type"] in RETAINED_EVENT_TYPES]
    victims = ordinary[:overflow]
    if len(victims) < overflow:
        victims.extend(protected[: overflow - len(victims)])
    conn.executemany("DELETE FROM orch_events WHERE id = ?", [(item,) for item in victims])
    return len(victims)


def seq_reserve_count(session_id: str, path: Path | None = None) -> int:
    """Сколько раз для сессии писали верхнюю отметку seq (не число событий)."""
    key = (str(resolve_db_path(path)), session_id)
    with _LOCK:
        return _RESERVES.get(key, 0)


def forget_seq_batch(session_id: str, path: Path | None = None) -> None:
    """Сбросить память пачки, как после рестарта процесса. Отметка в БД остаётся."""
    key = (str(resolve_db_path(path)), session_id)
    with _LOCK:
        _BATCHES.pop(key, None)


def _event_conn(path: Path) -> Any:
    """Одно соединение на файл. Вызывать под ``_LOCK``."""
    key = str(path)
    conn = _CONNS.get(key)
    if conn is not None:
        return conn
    from app.orchestrator.db.migrations import ensure_migrated

    ensure_migrated(path)
    import sqlite3

    conn = sqlite3.connect(key, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL").fetchone()
    _CONNS[key] = conn
    return conn


def _take_seq(path: Path, session_id: str) -> int:
    """Следующий seq. БД обновляется пачкой ``SEQ_RESERVE``, не на каждый токен."""
    key = (str(path), session_id)
    with _LOCK:
        window = _BATCHES.get(key)
        if window is None or window[0] > window[1]:
            conn = _event_conn(path)
            row = conn.execute(
                "SELECT seq FROM orch_event_seq WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            current = int(row["seq"]) if row is not None else 0
            high = current + SEQ_RESERVE
            if row is None:
                conn.execute(
                    "INSERT INTO orch_event_seq (session_id, seq) VALUES (?, ?)",
                    (session_id, high),
                )
            else:
                conn.execute(
                    "UPDATE orch_event_seq SET seq = ? WHERE session_id = ?",
                    (high, session_id),
                )
            conn.commit()
            window = [current + 1, high]
            _BATCHES[key] = window
            _RESERVES[key] = _RESERVES.get(key, 0) + 1
        seq = window[0]
        window[0] = seq + 1
        return seq


def _insert_event(
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    task_id: int | None,
    created_at: str | None,
    persist: bool,
) -> tuple[int, str]:
    path = resolve_db_path()
    with _LOCK:
        conn = _event_conn(path)
        seq = _take_seq(path, session_id)
        stamp = created_at or _utc_stamp(conn)
        if persist:
            conn.execute(
                """
                INSERT INTO orch_events (session_id, task_id, seq, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    task_id,
                    seq,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    stamp,
                ),
            )
            conn.commit()
        return seq, stamp


def _utc_stamp(conn: Any) -> str:
    del conn
    from app.orchestrator.db.migrations import utc_now

    return utc_now()


def _load_persisted(session_id: str, last_seq: int) -> list[OrchEvent]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT session_id, task_id, seq, type, payload_json
            FROM orch_events
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (session_id, last_seq),
        ).fetchall()
    finally:
        conn.close()
    events: list[OrchEvent] = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        events.append(
            OrchEvent(
                type=row["type"],
                task_id=row["task_id"],
                payload=payload if isinstance(payload, dict) else {"value": payload},
                seq=int(row["seq"]),
                session_id=row["session_id"],
            )
        )
    return events


def persisted_types(session_id: str) -> list[str]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT type FROM orch_events WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [row["type"] for row in rows]
