from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

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


def _m0003_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_calls_ts ON api_calls(timestamp DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_calls_user ON api_calls(user_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_calls_provider_model ON api_calls(provider, model)")


def _m0004_custom_pricing(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS custom_pricing ("
        "model TEXT PRIMARY KEY, pricing_json TEXT NOT NULL, updated_at TEXT NOT NULL)")


MIGRATIONS = (
    ("0001_api_calls", _m0001_api_calls),
    ("0002_legacy_columns", _m0002_legacy_columns),
    ("0003_indexes", _m0003_indexes),
    ("0004_custom_pricing", _m0004_custom_pricing),
)


def migrate_tracker_db(conn: sqlite3.Connection) -> list[str]:
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
