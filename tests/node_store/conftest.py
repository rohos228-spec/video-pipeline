"""Схема node_defs / node_prompt_versions для тестов.

SQL — копия миграций PR #346 (коммит fab1b126), файл schema_346.sql.
Рантайм-миграции пишет только WS3; здесь схема поднимается в фикстуре.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

_SCHEMA = (Path(__file__).parent / "schema_346.sql").read_text(encoding="utf-8")


def _regexp(pattern: str, value: object) -> int:
    if value is None:
        return 0
    return 1 if re.search(pattern, str(value)) else 0


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.create_function("regexp", 2, _regexp)
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
    connection.create_function("regexp", 2, _regexp)
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
