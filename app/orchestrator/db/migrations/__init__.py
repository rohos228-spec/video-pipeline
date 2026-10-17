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
from datetime import UTC, datetime
from pathlib import Path

from app.settings import settings

_MIGRATIONS_DIR = Path(__file__).resolve().parent
_NODE_KEY_RE = re.compile(r"^n_[a-z0-9_]+$")


def utc_now() -> str:
    """ISO-строка UTC, лексикографически сравнимая."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _regexp(pattern: str, value: object) -> int:
    if value is None:
        return 0
    return 1 if re.search(pattern, str(value)) else 0


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Соединение с БД студии (``settings.sqlite_path``) и применённые миграции.

    Путь читается в момент вызова, чтобы тесты могли подменить ``sqlite_path``.
    """
    db_path = Path(path) if path is not None else Path(settings.sqlite_path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.create_function("regexp", 2, _regexp)
    apply_migrations(conn)
    return conn


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
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO orch_schema_migrations (name, applied_at) VALUES (?, ?)",
            (path.name, utc_now()),
        )
        conn.commit()
        done.append(path.name)
    return done


def node_key_ok(value: str) -> bool:
    return _NODE_KEY_RE.fullmatch(value or "") is not None
