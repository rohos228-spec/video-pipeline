"""Горячий путь событий: токены не пишут БД по одному и не ловят database is locked."""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from app.orchestrator.db.migrations import connect, resolve_db_path
from app.orchestrator.events import (
    SEQ_RESERVE,
    EventBus,
    forget_seq_batch,
    persisted_types,
    seq_reserve_count,
)


@pytest.mark.asyncio
async def test_token_burst_does_not_lock_and_reserves_seq_in_batches() -> None:
    session = "load-1"
    bus = EventBus()
    path = resolve_db_path()
    errors: list[BaseException] = []

    def _hammer() -> None:
        conn = sqlite3.connect(str(path), timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("CREATE TABLE IF NOT EXISTS orch_load_hammer (n INTEGER)")
            for n in range(80):
                conn.execute("INSERT INTO orch_load_hammer (n) VALUES (?)", (n,))
                conn.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    writer = threading.Thread(target=_hammer)
    writer.start()
    try:
        await asyncio.gather(*(bus.publish(session, "token", {"text": str(n)}) for n in range(200)))
    except sqlite3.OperationalError as exc:
        pytest.fail(f"database is locked: {exc}")
    writer.join(timeout=10)
    assert not errors
    assert "token" not in persisted_types(session)
    reserves = seq_reserve_count(session)
    assert 1 <= reserves <= (200 // SEQ_RESERVE) + 1
    assert reserves < 200

    forget_seq_batch(session)
    persisted = await bus.publish(session, "ack", {"text": "after-restart"})
    assert persisted.seq > 200
    assert "ack" in persisted_types(session)


def test_migrations_apply_once_per_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.orchestrator.db.migrations as mig

    mig._MIGRATED.clear()
    calls = {"n": 0}
    real = mig.apply_migrations

    def _wrapped(conn: object) -> list[str]:
        calls["n"] += 1
        return real(conn)  # type: ignore[arg-type]

    monkeypatch.setattr(mig, "apply_migrations", _wrapped)
    first = connect()
    first.close()
    second = connect()
    second.close()
    assert calls["n"] == 1
    conn = connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"
