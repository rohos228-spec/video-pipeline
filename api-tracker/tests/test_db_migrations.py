from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

TRACKER_DIR = Path(__file__).resolve().parent.parent
if str(TRACKER_DIR) not in sys.path:
    sys.path.insert(0, str(TRACKER_DIR))

from app import db_schema as ts
from app.db import init_db


def _cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(api_calls)").fetchall()}


def _objects(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}


def test_fresh_db_gets_all_migrations_and_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    init_db(db)
    with closing(sqlite3.connect(db)) as c:
        ids = [r[0] for r in c.execute(f"SELECT id FROM {ts.MIGRATIONS_TABLE} ORDER BY id")]
        assert ids == sorted(m for m, _ in ts.MIGRATIONS)
        assert {"user_name", "device_name", "synced", "metadata_json"} <= _cols(c)
        assert {"idx_api_calls_ts", "idx_api_calls_user", "idx_api_calls_provider_model",
                "custom_pricing"} <= _objects(c)
        assert ts.migrate_tracker_db(c) == []


def test_legacy_db_gets_missing_columns_and_keeps_rows(tmp_path):
    db = tmp_path / "t.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
                  "provider TEXT NOT NULL, model TEXT NOT NULL, cost_usd REAL DEFAULT 0.0)")
        c.execute("INSERT INTO api_calls (timestamp, provider, model, cost_usd) VALUES ('t','kie','m',1.5)")
        c.commit()
    init_db(db)
    with closing(sqlite3.connect(db)) as c:
        row = c.execute("SELECT user_name, device_name, synced, cost_usd FROM api_calls").fetchone()
        assert "idx_api_calls_user" in _objects(c)
    assert row == ("unknown", "", 1, 1.5)


def test_db_already_migrated_by_the_main_pipeline_is_only_recorded(tmp_path):
    db = tmp_path / "t.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute(ts._API_CALLS_DDL)
        c.execute(f"CREATE TABLE {ts.MIGRATIONS_TABLE} (id TEXT NOT NULL PRIMARY KEY, applied_at TEXT NOT NULL)")
        c.executemany(f"INSERT INTO {ts.MIGRATIONS_TABLE} VALUES (?, 'x')",
                      [("0001_api_calls",), ("0002_legacy_columns",)])
        c.commit()
        applied = ts.migrate_tracker_db(c)
    assert applied == ["0003_indexes", "0004_custom_pricing"]


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
        tables = _objects(c)
    assert ids == {"0001_api_calls"}
    assert "half_done" not in tables


def test_init_db_has_no_raw_alter():
    src = (TRACKER_DIR / "app" / "db.py").read_text(encoding="utf-8")
    assert "ALTER TABLE" not in src
