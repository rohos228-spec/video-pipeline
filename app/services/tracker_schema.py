"""Versioned schema for the local api-tracker SQLite database (tracker.db).

Replaces the ad-hoc ``CREATE TABLE`` + ``PRAGMA table_info`` + ``ALTER TABLE`` that ran on every recorded
call. Each migration runs in one transaction together with its bookkeeping row in ``tracker_schema_migrations``;
a failing migration is rolled back whole, leaves no row and is raised to the caller (the hook logs it and the
pipeline never stops because of telemetry). Migrations are idempotent: an old database that already has the
columns is only recorded. Nothing is dropped or rewritten. After a successful run the database path is
remembered in-process, so the steady-state call does one set lookup, not a schema scan.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401  (db_path may be a Path)

MIGRATIONS_TABLE = "tracker_schema_migrations"

_API_CALLS_DDL = """
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT 'unknown',
    device_name TEXT DEFAULT '',
    provider TEXT NOT NULL,
    key_alias TEXT DEFAULT '',
    model TEXT NOT NULL,
    call_type TEXT DEFAULT 'text',
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    media_count INTEGER DEFAULT 0,
    duration_sec REAL DEFAULT 0.0,
    cost_usd REAL DEFAULT 0.0,
    status_code INTEGER DEFAULT 200,
    error_message TEXT DEFAULT '',
    project_source TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    synced INTEGER DEFAULT 1
)"""

# Columns older databases may lack: (name, exact legacy DDL). Same DDL the old inline code used.
_LEGACY_COLUMNS = (
    ("user_name", "TEXT DEFAULT 'unknown'"),
    ("device_name", "TEXT DEFAULT ''"),
    ("synced", "INTEGER DEFAULT 1"),
)


def _m0001_api_calls(conn: sqlite3.Connection) -> None:
    conn.execute(_API_CALLS_DDL)


def _m0002_legacy_columns(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(api_calls)").fetchall()}
    for name, ddl in _LEGACY_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE api_calls ADD COLUMN {name} {ddl}")


MIGRATIONS = (
    ("0001_api_calls", _m0001_api_calls),
    ("0002_legacy_columns", _m0002_legacy_columns),
)

_ready: set[str] = set()
_lock = threading.Lock()


def migrate_tracker_db(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations; return the ids applied now. Raises on failure after rolling that one back."""
    prev = conn.isolation_level
    conn.isolation_level = None  # explicit BEGIN/COMMIT below
    applied: list[str] = []
    try:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} "
                     "(id TEXT NOT NULL PRIMARY KEY, applied_at TEXT NOT NULL)")
        done = {r[0] for r in conn.execute(f"SELECT id FROM {MIGRATIONS_TABLE}").fetchall()}
        for mid, fn in MIGRATIONS:
            if mid in done:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                fn(conn)
                conn.execute(f"INSERT OR IGNORE INTO {MIGRATIONS_TABLE} (id, applied_at) VALUES (?, ?)",
                             (mid, datetime.now(timezone.utc).isoformat()))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            applied.append(mid)
    finally:
        conn.isolation_level = prev
    return applied


def ensure_tracker_schema(conn: sqlite3.Connection, db_path: Path | str) -> None:
    """Run migrations once per database path per process (re-run if the file was deleted)."""
    key = str(db_path)
    if key in _ready:
        # One cheap read: a deleted/recreated file has no bookkeeping table, so it is migrated again.
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (MIGRATIONS_TABLE,)).fetchone()
        if has:
            return
    with _lock:
        migrate_tracker_db(conn)
        _ready.add(key)
