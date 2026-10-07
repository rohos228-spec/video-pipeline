"""Versioned migrations for the local api-tracker database (replaces per-call inline ALTER)."""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from app.services import tracker_schema as ts


@pytest.fixture(autouse=True)
def _clear_ready():
    ts._ready.clear()
    yield
    ts._ready.clear()


def _cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(api_calls)").fetchall()}


def test_fresh_db_gets_all_migrations(tmp_path):
    db = tmp_path / "t.db"
    with closing(sqlite3.connect(db)) as c:
        applied = ts.migrate_tracker_db(c)
        assert applied == [m for m, _ in ts.MIGRATIONS]
        assert {"user_name", "device_name", "synced", "metadata_json"} <= _cols(c)
        assert ts.migrate_tracker_db(c) == []  # idempotent


def test_legacy_db_gets_missing_columns_and_keeps_rows(tmp_path):
    db = tmp_path / "t.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
                  "provider TEXT NOT NULL, model TEXT NOT NULL, cost_usd REAL DEFAULT 0.0)")
        c.execute("INSERT INTO api_calls (timestamp, provider, model, cost_usd) VALUES ('t','kie','m',1.5)")
        c.commit()
        ts.migrate_tracker_db(c)
        row = c.execute("SELECT user_name, device_name, synced, cost_usd FROM api_calls").fetchone()
    assert row == ("unknown", "", 1, 1.5)


def test_failing_migration_rolls_back_and_records_nothing(tmp_path, monkeypatch):
    db = tmp_path / "t.db"

    def boom(conn):
        conn.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("boom")

    monkeypatch.setattr(ts, "MIGRATIONS", (("0001_api_calls", ts._m0001_api_calls), ("0002_bad", boom)))
    with closing(sqlite3.connect(db)) as c:
        with pytest.raises(RuntimeError):
            ts.migrate_tracker_db(c)
        ids = {r[0] for r in c.execute(f"SELECT id FROM {ts.MIGRATIONS_TABLE}")}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert ids == {"0001_api_calls"}
    assert "half_done" not in tables


def test_ensure_runs_once_per_path_and_reruns_after_file_deleted(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    calls = []
    real = ts.migrate_tracker_db
    monkeypatch.setattr(ts, "migrate_tracker_db", lambda c: calls.append(1) or real(c))
    with closing(sqlite3.connect(db)) as c:
        ts.ensure_tracker_schema(c, db)
        ts.ensure_tracker_schema(c, db)
    assert len(calls) == 1
    db.unlink()
    with closing(sqlite3.connect(db)) as c:
        ts.ensure_tracker_schema(c, db)
        assert "synced" in _cols(c)
    assert len(calls) == 2


def test_hook_writes_through_migrated_schema(tmp_path, monkeypatch):
    import time
    from app.services import api_tracker_hook as hook
    db = tmp_path / "tracker.db"
    monkeypatch.setattr(hook, "_TRACKER_DB", db)
    monkeypatch.setattr(hook, "_get_identity", lambda: ("tester", "dev"))
    monkeypatch.setattr(hook, "_push_to_supabase", lambda p: True)
    hook.record_api_call(provider="kie", model="m", status_code=200)
    end = time.time() + 5
    while time.time() < end:
        try:
            with closing(sqlite3.connect(db)) as c:
                n = c.execute("SELECT count(*) FROM api_calls").fetchone()[0]
                m = c.execute(f"SELECT count(*) FROM {ts.MIGRATIONS_TABLE}").fetchone()[0]
            if n:
                break
        except sqlite3.Error:
            pass
        time.sleep(0.05)
    assert n == 1 and m == len(ts.MIGRATIONS)
