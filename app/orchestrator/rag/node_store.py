"""Адаптер node_store (владелец — WS2).

WS6 не импортирует ``app/orchestrator/node_store``. Чтение git-baseline и
DB-override идёт через этот протокол.

Таблица (чтение, не миграция), как в PR #346 после ``fab1b126``:

* ``node_defs.active_version`` — единственный указатель активной DB-версии.
  ``NULL`` = git-шаблон (``source=git``, ``db_version=None``).
* ``node_defs.updated_change_id`` — вместе с ``active_version`` входит в ревизию
  источника, включая переход указателя в ``NULL``.
* ``node_prompt_versions`` без ``is_active``. История неизменяема.
  ``kind`` включает ``revert_to_git``: такие строки никогда не индексируются
  как активные. ``base_git_sha256`` — хеш git-файла на момент записи версии
  (для ``stale_base``). ``created_via`` — ``orchestrator|studio_ui``.

Рантайм-снимок — ``NodeDef``: ``source``, ``version``, ``db_version``,
``git_sha256``, ``stale_base``. Колонка ``active_version`` на снимок не копируется.
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
    # Указатель node_defs. NULL = «сейчас git». В metadata чанка не кладётся.
    pointer_active_version: int | None = None
    updated_change_id: str | None = None
    follow_git: bool = False

    @property
    def revision_token(self) -> str:
        return json.dumps(
            {
                "change_id": self.change_id,
                "db_version": self.db_version,
                "git_sha256": self.git_sha256,
                "params": self.params,
                "pointer_active_version": self.pointer_active_version,
                "prompt_template": self.prompt_template,
                "source": self.source,
                "stale_base": self.stale_base,
                "status": self.status,
                "title": self.title,
                "updated_change_id": self.updated_change_id,
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
    """DB-указатель поверх git.

    ``follow_git`` (``active_version IS NULL`` или ``revert_to_git``) оставляет
    текст git-файла: ``source=git``, ``db_version=None``, ``stale_base=False``.
    Иначе индексируется строка версии. ``git_sha256`` снимка — хеш текущего
    git-файла. ``stale_base`` — ``base_git_sha256`` версии не совпал с ним.
    """
    if override.follow_git:
        return _follow_git(base, override)
    current_git = base.git_sha256 if base is not None else None
    recorded = override.git_sha256
    stale = bool(current_git and recorded and current_git != recorded)
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
        git_sha256=current_git,
        stale_base=stale,
        params=params,
        change_id=override.change_id,
        updated_at=override.updated_at or (base.updated_at if base else None),
        description=override.description or (base.description if base else ""),
        prompt_path=base.prompt_path if base else override.prompt_path,
        pointer_active_version=override.pointer_active_version,
        updated_change_id=override.updated_change_id,
        follow_git=False,
    )


def _follow_git(base: ResolvedNode | None, override: ResolvedNode) -> ResolvedNode:
    if base is None:
        return ResolvedNode(
            node_key=override.node_key,
            group_key=override.group_key,
            title=override.title or override.node_key,
            status=override.status,
            prompt_template="",
            source="git",
            version=0,
            db_version=None,
            git_sha256=None,
            stale_base=False,
            params={},
            updated_at=override.updated_at,
            description=override.description,
            pointer_active_version=None,
            updated_change_id=override.updated_change_id,
            follow_git=True,
        )
    return ResolvedNode(
        node_key=base.node_key,
        group_key=override.group_key or base.group_key,
        title=override.title or base.title,
        status=override.status or base.status,
        prompt_template=base.prompt_template,
        source="git",
        version=base.version,
        db_version=None,
        git_sha256=base.git_sha256,
        stale_base=False,
        params=dict(base.params),
        change_id=None,
        updated_at=override.updated_at or base.updated_at,
        description=override.description or base.description,
        prompt_path=base.prompt_path,
        pointer_active_version=None,
        updated_change_id=override.updated_change_id,
        follow_git=True,
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
                version = _selected_version(conn, node_key, record, version_cols)
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


def _selected_version(
    conn: sqlite3.Connection,
    node_key: str,
    record: dict[str, Any],
    cols: set[str],
) -> dict[str, Any] | None:
    """Строка, на которую указывает ``active_version``.

    ``NULL``, отсутствие строки и ``kind=revert_to_git`` → ``None`` (индексировать git).
    Колонка ``is_active`` не читается: её больше нет.
    """
    if not cols or record.get("active_version") is None:
        return None
    active_version = record.get("active_version")
    rows = [
        _row_dict(row)
        for row in conn.execute(
            "SELECT * FROM node_prompt_versions WHERE node_key = ? AND version = ?",
            (node_key, active_version),
        )
    ]
    if not rows:
        return None
    chosen = rows[0]
    if str(chosen.get("kind") or "") == "revert_to_git":
        return None
    return chosen


def _node_from_record(
    record: dict[str, Any],
    version: dict[str, Any] | None,
    cols: set[str],
) -> ResolvedNode:
    updated_change_id = _as_str(record.get("updated_change_id")) if "updated_change_id" in cols else None
    updated_at = _as_str(record.get("updated_at"))
    title = str(record.get("title") or record["node_key"])
    status = str(record.get("status") or "active")
    group_key = str(record.get("group_key") or "")
    description = str(record.get("description") or "") if "description" in cols else ""
    if version is None:
        return ResolvedNode(
            node_key=str(record["node_key"]),
            group_key=group_key,
            title=title,
            status=status,
            prompt_template="",
            source="git",
            version=0,
            db_version=None,
            git_sha256=None,
            stale_base=False,
            updated_at=updated_at,
            description=description,
            pointer_active_version=None,
            updated_change_id=updated_change_id,
            follow_git=True,
        )
    params = _parse_params(version.get("params_json"))
    resolved_version = int(version.get("version") or 0)
    recorded_git = _as_str(version.get("base_git_sha256")) if "base_git_sha256" in version else None
    return ResolvedNode(
        node_key=str(record["node_key"]),
        group_key=group_key,
        title=title,
        status=status,
        prompt_template=str(version.get("prompt_template") or ""),
        source="db",
        version=resolved_version,
        db_version=resolved_version,
        git_sha256=recorded_git,
        stale_base=False,
        params=params,
        change_id=(None if version.get("change_id") in (None, "") else str(version.get("change_id"))),
        updated_at=_as_str(version.get("created_at") or updated_at),
        description=description,
        pointer_active_version=resolved_version,
        updated_change_id=updated_change_id,
        follow_git=False,
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


def _as_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
