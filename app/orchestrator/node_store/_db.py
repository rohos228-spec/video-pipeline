"""Соединение, транзакция и проверки ключей. Схему не создаёт: её пишет WS3."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

NODE_KEY_RE = re.compile(r"^n_[a-z0-9_]+$")
GROUP_KEY_RE = re.compile(r"^[a-z0-9_]+$")
TITLE_MAX = 120

CREATED_VIA = frozenset({"orchestrator", "studio_ui"})
ORIGINS = frozenset({"db", "git_override"})

REPLACE_MODES = frozenset({"replace", "replace_full"})
SECTION_MODES = frozenset({"section", "replace_section"})
APPEND_MODES = frozenset({"append_section"})
PATCH_MODES = REPLACE_MODES | SECTION_MODES | APPEND_MODES


class NodeStoreError(Exception):
    """Нарушение контракта node_store (не ограничение SQLite)."""


class NodeNotFoundError(NodeStoreError):
    """Нет ни строки node_defs, ни git-шаблона."""


class NodeExistsError(NodeStoreError):
    """create_node для уже записанного node_key."""


def utc_now() -> str:
    """ISO UTC, как в миграциях WS3: лексикографически сравнимая строка."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _regexp(pattern: str, value: object) -> int:
    if value is None:
        return 0
    return 1 if re.search(pattern, str(value)) else 0


def ensure_conn(conn: sqlite3.Connection) -> None:
    """Включить FK и REGEXP, без которых CHECK из миграции #346 не исполняется.

    PRAGMA foreign_keys внутри уже открытой транзакции SQLite игнорирует,
    поэтому вызывающий, который сам начал транзакцию, включает FK заранее.
    """
    if not conn.in_transaction:
        conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function("regexp", 2, _regexp)


def validate_node_key(node_key: str) -> str:
    key = (node_key or "").strip()
    if not NODE_KEY_RE.fullmatch(key):
        raise ValueError("node_key должен соответствовать ^n_[a-z0-9_]+$")
    return key


def validate_group_key(group_key: str) -> str:
    key = (group_key or "").strip()
    if not GROUP_KEY_RE.fullmatch(key):
        raise ValueError("group_key должен соответствовать ^[a-z0-9_]+$")
    return key


def validate_title(title: str) -> str:
    text = (title or "").strip()
    if not text:
        raise ValueError("title пуст")
    if len(text) > TITLE_MAX:
        raise ValueError("title длиннее 120 символов")
    return text


def validate_change_id(change_id: str) -> str:
    if not isinstance(change_id, str):
        raise ValueError("change_id должен быть строкой")
    text = change_id.strip()
    if not text:
        raise ValueError("change_id пуст")
    return text


def validate_created_via(created_via: str) -> str:
    if created_via not in CREATED_VIA:
        raise ValueError("created_via должен быть orchestrator или studio_ui")
    return created_via


def validate_origin(origin: str) -> str:
    if origin not in ORIGINS:
        raise ValueError("origin должен быть db или git_override")
    return origin


def validate_reason(reason: str, *, required: bool) -> str | None:
    if reason is None:
        if required:
            raise ValueError("reason обязателен")
        return None
    if not isinstance(reason, str):
        raise ValueError("reason должен быть строкой")
    text = reason.strip()
    if not text:
        if required:
            raise ValueError("reason пуст")
        return None
    return text


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """Одна транзакция записи.

    Внешней транзакции нет — коммит или полный откат через контекст sqlite3.
    Внешняя уже есть — SAVEPOINT, чтобы не закоммитить чужую работу.
    """
    ensure_conn(conn)
    if conn.in_transaction:
        conn.execute("SAVEPOINT node_store_write")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO SAVEPOINT node_store_write")
            conn.execute("RELEASE SAVEPOINT node_store_write")
            raise
        else:
            conn.execute("RELEASE SAVEPOINT node_store_write")
        return
    with conn:
        yield
