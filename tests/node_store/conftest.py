"""Схема node_defs / node_prompt_versions для тестов.

SQL — снимок миграций PR #346 на e4c3745b, файл schema_346.sql.
Фикстура применяет его явно через executescript. connect() WS3
миграции на каждый вызов больше не гоняет, и этих файлов в ветке нет.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_SCHEMA = (Path(__file__).parent / "schema_346.sql").read_text(encoding="utf-8")


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_SCHEMA)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def autocommit_conn(tmp_path: Path) -> sqlite3.Connection:
    """Соединение в autocommit: транзакцию держит только явный BEGIN."""
    connection = sqlite3.connect(tmp_path / "node_store.db")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_SCHEMA)
    connection.isolation_level = None
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def templates(tmp_path: Path) -> Path:
    root = tmp_path / "node_groups"
    root.mkdir()
    return root
