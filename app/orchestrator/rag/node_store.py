"""Локальный fallback ``resolve_node`` / ``get_node``, пока нет PR #349.

После мёржа ``cursor/ws2-node-store-ab29`` чтение ноды заменяется одним импортом
в ``app.orchestrator.rag.node_api``. Этот модуль повторяет сигнатуру и семантику,
не код WS2.

``resolve_node(node_key, conn, *, templates_root=None)`` → снимок с полями
``source``, ``version``, ``db_version``, ``git_sha256``, ``stale_base``.
``active_version IS NULL`` или отсутствие строки — git, ``version=0``,
``db_version=None``, ``stale_base=False``. Указатель на ``revert_to_git``
или на отсутствующую версию — ошибка, не тихий NULL.
``git_sha256`` — sha256 байтов файла ``templates/node_groups/**/<node_key>.md|json``.
Файлы housepc, названные по имени промпта, а не по ``node_key``, этот fallback
не сопоставляет: это сделает WS2. Промпты групп из репозитория по-прежнему
читает обход ``NODE_GROUPS``.
"""

from __future__ import annotations

import hashlib
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
    """sha256 текста промпта, если отдельного файла ``<node_key>`` нет.

    У файла шаблона ``NodeDef.git_sha256`` — хеш байтов файла целиком,
    его считает ``resolve_node``.
    """
    return sha256_text(prompt_template)


def merge_resolved(base: ResolvedNode | None, override: ResolvedNode) -> ResolvedNode:
    """DB-указатель поверх git.

    ``follow_git`` (``active_version IS NULL``) оставляет текст git-обхода:
    ``source=git``, ``db_version=None``, ``stale_base=False``.
    Иначе снимок берётся из ``resolve_node``: ``git_sha256`` и ``stale_base``
    уже посчитаны там и здесь не пересчитываются.
    """
    if override.follow_git:
        return _follow_git(base, override)
    current_git = base.git_sha256 if base is not None else None
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
        git_sha256=override.git_sha256 if override.git_sha256 is not None else current_git,
        stale_base=bool(override.stale_base),
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
            prompt_template=override.prompt_template,
            source="git",
            version=0,
            db_version=None,
            git_sha256=override.git_sha256,
            stale_base=False,
            params=dict(override.params),
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

    def list_overrides(self, *, templates_root: Path | None = None) -> list[ResolvedNode]: ...

    def resolve_node(self, node_key: str) -> ResolvedNode | None: ...


class MemoryNodeStore:
    """In-memory реализация для тестов и eval-харнесса."""

    def __init__(self, nodes: list[ResolvedNode] | None = None) -> None:
        self._nodes = {node.node_key: node for node in nodes or []}

    def list_overrides(self, *, templates_root: Path | None = None) -> list[ResolvedNode]:
        del templates_root
        return list(self._nodes.values())

    def resolve_node(self, node_key: str) -> ResolvedNode | None:
        return self._nodes.get(node_key)

    def upsert(self, node: ResolvedNode) -> None:
        self._nodes[node.node_key] = node


class SqliteNodeStore:
    """Читает ``node_defs`` + активную строку ``node_prompt_versions``, если таблицы есть."""

    def __init__(self, db_path: Path | None) -> None:
        self.db_path = Path(db_path) if db_path else None

    def list_overrides(self, *, templates_root: Path | None = None) -> list[ResolvedNode]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            return _overrides_via_resolve(conn, templates_root)
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


class NodeStoreError(Exception):
    """Контракт resolve_node нарушен: битый указатель или шаблон."""


class NodeNotFoundError(NodeStoreError):
    """Нет ни строки node_defs, ни файла шаблона с именем node_key."""


@dataclass
class NodeDef:
    """Снимок той же формы, что вернёт WS2: без active_version и is_active."""

    node_key: str
    prompt_template: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "git"
    version: int = 0
    db_version: int | None = None
    git_sha256: str | None = None
    stale_base: bool = False


def resolve_node(
    node_key: str,
    conn: sqlite3.Connection,
    *,
    templates_root: Path | None = None,
) -> NodeDef:
    """Собрать снимок ноды. Семантика как у WS2 ``resolve_node``, реализация своя.

    Git-файл ищется только как ``<node_key>.md`` / ``<node_key>.json`` под
    ``templates/node_groups``. Имя по промпту (housepc) не разбирается.
    """
    key = _validate_node_key(node_key)
    baseline = _GitFile.find(key, templates_root)
    active = _active_version(conn, key)
    if active is _MISSING and not baseline.found:
        raise NodeNotFoundError(f"нода {key} не найдена ни в БД, ни в git")
    if active is _MISSING or active is None:
        return _git_def(key, baseline)
    version = int(active)
    stored = _version_row(conn, key, version)
    if stored is None:
        raise NodeStoreError(f"active_version {version} ноды {key} не найден в истории")
    if str(stored.get("kind") or "") == "revert_to_git":
        raise NodeStoreError("active_version не может указывать на revert_to_git")
    params = _params_object(stored.get("params_json"), key, version)
    base_hash = stored.get("base_git_sha256")
    base_hash = None if base_hash in (None, "") else str(base_hash)
    return NodeDef(
        node_key=key,
        prompt_template=str(stored.get("prompt_template") or ""),
        params=params,
        source="db",
        version=version,
        db_version=version,
        git_sha256=baseline.sha256,
        stale_base=base_hash != baseline.sha256,
    )


def get_node(
    node_key: str,
    conn: sqlite3.Connection,
    *,
    templates_root: Path | None = None,
) -> NodeDef:
    """Тот же контракт, что у ``resolve_node``."""
    return resolve_node(node_key, conn, templates_root=templates_root)


_MISSING = object()


def _validate_node_key(node_key: str) -> str:
    key = (node_key or "").strip()
    if not NODE_KEY_RE.fullmatch(key):
        raise ValueError("node_key должен соответствовать ^n_[a-z0-9_]+$")
    return key


def _default_templates_root() -> Path:
    from app.project_root import find_project_root

    return find_project_root() / "templates" / "node_groups"


@dataclass(frozen=True)
class _GitFile:
    found: bool
    prompt_template: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    sha256: str | None = None

    @staticmethod
    def find(node_key: str, templates_root: Path | None) -> _GitFile:
        root = templates_root if templates_root is not None else _default_templates_root()
        if not root.is_dir():
            return _GitFile(found=False)
        matches = [
            path
            for name in (f"{node_key}.json", f"{node_key}.md")
            for path in root.rglob(name)
            if path.is_file()
        ]
        unique = sorted(set(matches))
        if not unique:
            return _GitFile(found=False)
        if len(unique) > 1:
            raise NodeStoreError(
                f"несколько git-шаблонов для {node_key}: " + ", ".join(str(path) for path in unique)
            )
        path = unique[0]
        raw = path.read_bytes()
        prompt, params = _prompt_of(path, raw)
        return _GitFile(
            found=True,
            prompt_template=prompt,
            params=params,
            sha256=hashlib.sha256(raw).hexdigest(),
        )


def _prompt_of(path: Path, raw: bytes) -> tuple[str, dict[str, Any]]:
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NodeStoreError(f"git-шаблон {path.name} не JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("prompt_template"), str):
            raise NodeStoreError(f"git-шаблон {path.name} без prompt_template")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise NodeStoreError(f"params в {path.name} должен быть объектом")
        return payload["prompt_template"], dict(params)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NodeStoreError(f"git-шаблон {path.name} не в utf-8") from exc
    return _markdown_body(text), {}


def _markdown_body(text: str) -> str:
    """Тело после первого блока ``---``. Так устроены шаблоны в этом репозитории."""
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            body = "".join(lines[index + 1 :])
            return body[1:] if body.startswith("\n") else body
    return text


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _active_version(conn: sqlite3.Connection, node_key: str) -> Any:
    if "node_defs" not in _table_names(conn):
        return _MISSING
    cols = _column_names(conn, "node_defs")
    if "node_key" not in cols:
        return _MISSING
    if "active_version" not in cols:
        row = conn.execute("SELECT 1 FROM node_defs WHERE node_key = ?", (node_key,)).fetchone()
        return _MISSING if row is None else None
    row = conn.execute(
        "SELECT active_version FROM node_defs WHERE node_key = ?",
        (node_key,),
    ).fetchone()
    if row is None:
        return _MISSING
    return row[0]


def _version_row(conn: sqlite3.Connection, node_key: str, version: int) -> dict[str, Any] | None:
    if "node_prompt_versions" not in _table_names(conn):
        return None
    cols = _column_names(conn, "node_prompt_versions")
    wanted = [
        name
        for name in ("prompt_template", "params_json", "base_git_sha256", "kind", "change_id", "created_at")
        if name in cols
    ]
    if "version" not in cols or "node_key" not in cols or not wanted:
        return None
    row = conn.execute(
        f"SELECT {', '.join(wanted)} FROM node_prompt_versions WHERE node_key = ? AND version = ?",
        (node_key, version),
    ).fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return {name: row[name] for name in row.keys()}  # noqa: SIM118
    return dict(zip(wanted, row, strict=True))


def _params_object(raw: Any, node_key: str, version: int) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise NodeStoreError(f"params_json ноды {node_key} версии {version} не JSON") from exc
    if not isinstance(parsed, dict):
        raise NodeStoreError(f"params_json ноды {node_key} версии {version} должен быть объектом")
    return parsed


def _git_def(node_key: str, baseline: _GitFile) -> NodeDef:
    return NodeDef(
        node_key=node_key,
        prompt_template=baseline.prompt_template,
        params=dict(baseline.params),
        source="git",
        version=0,
        db_version=None,
        git_sha256=baseline.sha256,
        stale_base=False,
    )


def _overrides_via_resolve(conn: sqlite3.Connection, templates_root: Path | None) -> list[ResolvedNode]:
    """Ключи из ``node_defs``. Промпт и хеш — только через ``resolve_node``."""
    from app.orchestrator.rag.node_api import bind_resolve

    resolve, not_found, _store_error = bind_resolve()
    nodes: list[ResolvedNode] = []
    for record in _identity_rows(conn):
        key = str(record.get("node_key") or "")
        if not NODE_KEY_RE.fullmatch(key):
            continue
        try:
            defined = resolve(key, conn, templates_root=templates_root)
        except not_found:
            continue
        node = _resolved_from_def(defined, record)
        if not node.follow_git:
            version_row = _version_row(conn, key, int(defined.version))
            change_id = None if version_row is None else version_row.get("change_id")
            node.change_id = None if change_id in (None, "") else str(change_id)
            created_at = None if version_row is None else version_row.get("created_at")
            if created_at not in (None, ""):
                node.updated_at = str(created_at)
        nodes.append(node)
    return nodes


def _identity_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Подписи нод для чанка. Версию промпта отсюда не читаем."""
    if "node_defs" not in _table_names(conn):
        return []
    cols = _column_names(conn, "node_defs")
    if "node_key" not in cols:
        return []
    wanted = [
        name
        for name in ("node_key", "group_key", "title", "status", "description", "updated_change_id", "updated_at")
        if name in cols
    ]
    rows = []
    for row in conn.execute(f"SELECT {', '.join(wanted)} FROM node_defs"):
        if isinstance(row, sqlite3.Row):
            rows.append({name: row[name] for name in wanted})
        else:
            rows.append(dict(zip(wanted, row, strict=True)))
    return rows


def _source_name(source: Any) -> str:
    value = getattr(source, "value", source)
    return str(value)


def _resolved_from_def(defined: Any, record: dict[str, Any]) -> ResolvedNode:
    source = _source_name(defined.source)
    follow = source == "git"
    updated_change_id = record.get("updated_change_id")
    updated_at = record.get("updated_at")
    return ResolvedNode(
        node_key=str(defined.node_key),
        group_key=str(record.get("group_key") or ""),
        title=str(record.get("title") or defined.node_key),
        status=str(record.get("status") or "active"),
        prompt_template=str(defined.prompt_template or ""),
        source=source,
        version=0 if follow else int(defined.version),
        db_version=None if follow else defined.db_version,
        git_sha256=defined.git_sha256,
        stale_base=False if follow else bool(defined.stale_base),
        params={} if follow else dict(defined.params or {}),
        updated_at=None if updated_at in (None, "") else str(updated_at),
        description=str(record.get("description") or ""),
        pointer_active_version=None if follow else int(defined.version),
        updated_change_id=None if updated_change_id in (None, "") else str(updated_change_id),
        follow_git=follow,
    )
