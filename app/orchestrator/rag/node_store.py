"""Адаптер node_store (владелец — WS2).

WS6 не импортирует ``app/orchestrator/node_store``. Чтение git-baseline и
DB-override идёт через этот протокол.

Таблица (чтение, не миграция), как в PR #346:

* ``node_defs(node_key UNIQUE ^n_[a-z0-9_]+$, group_key, title,
  origin db|git_override, status active|disabled, active_version, ...)``
* ``node_prompt_versions(node_key, version, prompt_template, params_json,
  kind create|edit_prompt|edit_params|rollback, is_active, change_id, created_at)``

Рантайм-снимок — утверждённый ``NodeDef``: ``source`` (git|db), ``version``,
``db_version``, ``git_sha256``, ``stale_base``. Полей ``overridden_in_db`` и
``active_version`` на снимке нет. Колонка ``active_version`` только выбирает
строку версии. Индексируется эта разрешённая версия. Ревизия включает
``version``, ``db_version``, ``git_sha256``, ``stale_base`` и ``change_id``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.orchestrator.rag.canon import sha256_text

NODE_KEY_RE = re.compile(r"^n_[a-z0-9_]+$")


@dataclass
class ResolvedNode:
    """Снимок разрешённой ноды. Поля ``NodeDef``: source, version, db_version, git_sha256, stale_base."""

    node_key: str
    group_key: str
    title: str
    status: str
    prompt_template: str
    source: str = "git"
    version: int = 1
    db_version: int | None = None
    git_sha256: str | None = None
    stale_base: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    change_id: str | None = None
    updated_at: str | None = None
    description: str = ""
    prompt_path: str | None = None

    @property
    def revision_token(self) -> str:
        return json.dumps(
            {
                "change_id": self.change_id,
                "db_version": self.db_version,
                "git_sha256": self.git_sha256,
                "params": self.params,
                "prompt_template": self.prompt_template,
                "source": self.source,
                "stale_base": self.stale_base,
                "status": self.status,
                "title": self.title,
                "version": self.version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def git_content_sha256(prompt_template: str) -> str:
    """sha256 текста git-промпта. Это ``NodeDef.git_sha256`` для baseline."""
    return sha256_text(prompt_template)


def merge_resolved(base: ResolvedNode | None, override: ResolvedNode) -> ResolvedNode:
    """DB-строка перекрывает git. Индексируется разрешённая (активная) версия.

    ``git_sha256`` снимка — хеш текущего git-промпта, если baseline есть.
    ``stale_base`` истинен, если строка БД так помечена или записанный в ней
    ``git_sha256`` не совпал с текущим файлом.
    """
    current_git = base.git_sha256 if base is not None else None
    recorded = override.git_sha256
    stale = bool(override.stale_base)
    if recorded and current_git and recorded != current_git:
        stale = True
    prompt = override.prompt_template or (base.prompt_template if base else "")
    params = override.params or (base.params if base else {})
    return ResolvedNode(
        node_key=override.node_key,
        group_key=override.group_key or (base.group_key if base else ""),
        title=override.title or (base.title if base else override.node_key),
        status=override.status,
        prompt_template=prompt,
        source="db",
        version=override.version,
        db_version=override.db_version if override.db_version is not None else override.version,
        git_sha256=current_git if base is not None else recorded,
        stale_base=stale,
        params=params,
        change_id=override.change_id,
        updated_at=override.updated_at or (base.updated_at if base else None),
        description=override.description or (base.description if base else ""),
        prompt_path=base.prompt_path if base else override.prompt_path,
    )


class NodeStore(Protocol):
    """Узкий порт WS2: список override и точечный resolve."""

    def list_overrides(self) -> list[ResolvedNode]: ...

    def resolve_node(self, node_key: str) -> ResolvedNode | None: ...


class MemoryNodeStore:
    """In-memory реализация для тестов и eval-харнесса."""

    def __init__(self, nodes: list[ResolvedNode] | None = None) -> None:
        self._nodes = {node.node_key: node for node in nodes or []}

    def list_overrides(self) -> list[ResolvedNode]:
        return list(self._nodes.values())

    def resolve_node(self, node_key: str) -> ResolvedNode | None:
        return self._nodes.get(node_key)

    def upsert(self, node: ResolvedNode) -> None:
        self._nodes[node.node_key] = node


class SqliteNodeStore:
    """Читает ``node_defs`` + активную строку ``node_prompt_versions``, если таблицы есть."""

    def __init__(self, db_path: Path | None) -> None:
        self.db_path = Path(db_path) if db_path else None

    def list_overrides(self) -> list[ResolvedNode]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "node_defs" not in tables:
                return []
            defs_cols = _columns(conn, "node_defs")
            version_cols = (
                _columns(conn, "node_prompt_versions") if "node_prompt_versions" in tables else set()
            )
            nodes: list[ResolvedNode] = []
            for row in conn.execute("SELECT * FROM node_defs"):
                record = _row_dict(row)
                node_key = str(record.get("node_key") or "")
                if not NODE_KEY_RE.match(node_key):
                    continue
                version = _active_version(conn, node_key, record, version_cols)
                nodes.append(_node_from_record(record, version, defs_cols))
            return nodes
        finally:
            conn.close()

    def resolve_node(self, node_key: str) -> ResolvedNode | None:
        for node in self.list_overrides():
            if node.node_key == node_key:
                return node
        return None

    def _connect(self) -> sqlite3.Connection | None:
        if self.db_path is None or not self.db_path.is_file():
            return None
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    # sqlite3.Row итерируется значениями, не именами колонок.
    return {name: row[name] for name in row.keys()}  # noqa: SIM118


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if table not in {"node_defs", "node_prompt_versions"}:
        raise ValueError(table)
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _active_version(
    conn: sqlite3.Connection,
    node_key: str,
    record: dict[str, Any],
    cols: set[str],
) -> dict[str, Any] | None:
    if not cols:
        return None
    active_version = record.get("active_version")
    rows = [
        _row_dict(row)
        for row in conn.execute(
            "SELECT * FROM node_prompt_versions WHERE node_key = ?",
            (node_key,),
        )
    ]
    active = [row for row in rows if _truthy(row.get("is_active"))]
    if active_version is not None:
        pinned = [row for row in active if row.get("version") == active_version]
        if pinned:
            return pinned[0]
        pinned_any = [row for row in rows if row.get("version") == active_version]
        if pinned_any:
            return pinned_any[0]
    if active:
        return max(active, key=lambda row: int(row.get("version") or 0))
    return None


def _node_from_record(
    record: dict[str, Any],
    version: dict[str, Any] | None,
    cols: set[str],
) -> ResolvedNode:
    version = version or {}
    params = _parse_params(version.get("params_json"))
    resolved_version = int(version.get("version") or record.get("active_version") or 1)
    recorded_git = _as_str(record.get("git_sha256")) if "git_sha256" in cols else None
    stale = _truthy(record.get("stale_base")) if "stale_base" in cols else False
    return ResolvedNode(
        node_key=str(record["node_key"]),
        group_key=str(record.get("group_key") or ""),
        title=str(record.get("title") or record["node_key"]),
        status=str(record.get("status") or "active"),
        prompt_template=str(version.get("prompt_template") or ""),
        source="db",
        version=resolved_version,
        db_version=resolved_version,
        git_sha256=recorded_git,
        stale_base=stale,
        params=params,
        change_id=(None if version.get("change_id") in (None, "") else str(version.get("change_id"))),
        updated_at=_as_str(version.get("created_at") or record.get("updated_at")),
        description=str(record.get("description") or "") if "description" in cols else "",
    )


def _parse_params(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _as_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
