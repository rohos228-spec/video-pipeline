from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

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


def backup_tracker_before_migration(conn: sqlite3.Connection) -> Path | None:
    from app.services.schema_migrations import backup_sqlite

    main = next((r[2] for r in conn.execute("PRAGMA database_list").fetchall() if r[1] == "main"), "")
    if not main or not Path(main).is_file():
        return None
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}
    done = ({r[0] for r in conn.execute(f"SELECT id FROM {MIGRATIONS_TABLE}").fetchall()}
            if MIGRATIONS_TABLE in tables else set())
    if not (tables - {MIGRATIONS_TABLE}) or all(mid in done for mid, _ in MIGRATIONS):
        return None
    return backup_sqlite(main, tag="pre-migration", keep=10, journal=False).path


def migrate_tracker_db(conn: sqlite3.Connection) -> list[str]:
    backup_tracker_before_migration(conn)
    prev = conn.isolation_level
    conn.isolation_level = None
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
    key = str(db_path)
    if key in _ready:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (MIGRATIONS_TABLE,)).fetchone()
        if has:
            return
    with _lock:
        migrate_tracker_db(conn)
        _ready.add(key)
