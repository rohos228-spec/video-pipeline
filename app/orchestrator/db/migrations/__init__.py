"""Идемпотентный раннер миграций оркестратора.

В репозитории нет каталога Alembic: схема студии создаётся через
``Base.metadata.create_all`` и точечные ALTER (``db_v2``). Для таблиц
оркестратора (D5) — отдельный пронумерованный каталог SQL рядом с этим
модулем. Повторный запуск безопасен: имя файла пишется в
``orch_schema_migrations``.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.settings import settings

_MIGRATIONS_DIR = Path(__file__).resolve().parent
_NODE_KEY_RE = re.compile(r"^n_[a-z0-9_]+$")
_ADD_COLUMN_RE = re.compile(
    r"^[ \t]*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\b[^;]*;",
    re.IGNORECASE | re.MULTILINE,
)
_MIGRATED: set[str] = set()
_MIGRATE_LOCK = threading.Lock()


def utc_now() -> str:
    """ISO-строка UTC, лексикографически сравнимая."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def resolve_db_path(path: Path | None = None) -> Path:
    """Абсолютный путь БД студии. ``None`` — ``settings.sqlite_path`` в момент вызова."""
    db_path = Path(path) if path is not None else Path(settings.sqlite_path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    return db_path


def ensure_migrated(path: Path | None = None) -> Path:
    """Применить миграции один раз на процесс для этого файла.

    Повторный вызов с тем же путём не открывает каталог SQL и не читает журнал.
    Старт процесса (``build_runtime``) вызывает это до горячего пути событий.
    """
    db_path = resolve_db_path(path)
    key = str(db_path)
    if key in _MIGRATED:
        return db_path
    with _MIGRATE_LOCK:
        if key in _MIGRATED:
            return db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key)
        try:
            conn.row_factory = sqlite3.Row
            _prepare(conn)
            apply_migrations(conn)
        finally:
            conn.close()
        _MIGRATED.add(key)
    return db_path


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Короткое соединение. Миграции уже применены ``ensure_migrated``.

    Горячий путь событий держит отдельное долгоживущее соединение, не это.
    """
    db_path = ensure_migrated(path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _prepare(conn)
    return conn


def _prepare(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL").fetchone()


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Применить ещё не записанные ``*.sql`` в лексикографическом порядке."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orch_schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {row[0] for row in conn.execute("SELECT name FROM orch_schema_migrations")}
    done: list[str] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        script = _without_existing_columns(conn, path.read_text(encoding="utf-8"))
        if script.strip():
            conn.executescript(script)
        conn.execute(
            "INSERT INTO orch_schema_migrations (name, applied_at) VALUES (?, ?)",
            (path.name, utc_now()),
        )
        conn.commit()
        done.append(path.name)
    return done


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _without_existing_columns(conn: sqlite3.Connection, sql: str) -> str:
    """Повторный ALTER после обрыва между файлом и журналом не дублирует колонку."""

    def skip(match: re.Match[str]) -> str:
        if _has_column(conn, match.group(1), match.group(2)):
            return ""
        return match.group(0)

    return _ADD_COLUMN_RE.sub(skip, sql)


def node_key_ok(value: str) -> bool:
    return _NODE_KEY_RE.fullmatch(value or "") is not None
