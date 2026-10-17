"""Сборщики четырёх корпусов: rules, history, nodes, prompts.

Git-шаблоны ``templates/node_groups/**`` читаются только на чтение.
На текущем main каталог часто пуст: baseline дополнительно берётся из
встроенных групп ``NODE_GROUPS`` и файлов ``templates/excel_gpt_agents``
(это отклонение от буквы плана, пока R0a не привёз ``templates/node_groups``).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.orchestrator.rag.chunking import split_history_text, split_prompt, split_rules, window_messages
from app.orchestrator.rag.node_store import (
    NODE_KEY_RE,
    MemoryNodeStore,
    ResolvedNode,
    SqliteNodeStore,
    git_content_sha256,
    merge_resolved,
)
from app.orchestrator.rag.types import SourceDoc

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

# У GroupNodeSpec нет поля role. Роль чанка — description спеки и её порядковый
# номер в NodeGroupDef.nodes (app/services/node_groups.py: NODE_GROUPS и
# пользовательские JSON). Ничего сверх этих полей не дописывается.
_ROLE_SOURCE = (
    "GroupNodeSpec.description и порядок в NodeGroupDef.nodes; "
    "явного поля role нет. Источник: app/services/node_groups.py"
)


_GROUP_ROLE_CACHE: dict[str, dict[str, Any]] | None = None
_version_fallbacks = 0
logger = logging.getLogger(__name__)


def invalidate_group_role_cache() -> None:
    """Сброс ролей после create_node / edit_node_prompt, без рестарта процесса."""
    global _GROUP_ROLE_CACHE
    _GROUP_ROLE_CACHE = None


def version_fallback_count() -> int:
    return _version_fallbacks


def reset_version_fallback_count() -> None:
    global _version_fallbacks
    _version_fallbacks = 0


def group_role_index() -> dict[str, dict[str, Any]]:
    """preferred_id ноды → роль в своей группе.

    Ключ — ``GroupNodeSpec.preferred_id``. При коллизии побеждает группа,
    которая позже в ``all_groups()`` (сначала встроенные, затем JSON).
    """
    global _GROUP_ROLE_CACHE
    if _GROUP_ROLE_CACHE is None:
        _GROUP_ROLE_CACHE = _build_group_role_index()
    return _GROUP_ROLE_CACHE


def _build_group_role_index() -> dict[str, dict[str, Any]]:
    try:
        from app.services.node_groups import all_groups

        groups = all_groups()
    except Exception:  # noqa: BLE001 — каталог групп не должен ронять индексацию
        try:
            from app.services.node_groups import NODE_GROUPS

            groups = NODE_GROUPS
        except Exception:  # noqa: BLE001
            return {}
    index: dict[str, dict[str, Any]] = {}
    for group in groups.values():
        size = len(group.nodes)
        for order, spec in enumerate(group.nodes, start=1):
            index[spec.preferred_id] = {
                "group_id": group.group_id,
                "group_title": group.title,
                "local_key": spec.local_key,
                "group_order": order,
                "group_size": size,
                "group_role": spec.description,
                "role_source": _ROLE_SOURCE,
            }
    return index


def _role_phrase(node: ResolvedNode) -> str:
    role = group_role_index().get(node.node_key)
    if not role:
        return ""
    duty = str(role["group_role"] or "").strip()
    place = (
        f"Роль в группе «{role['group_title']}», "
        f"позиция {role['group_order']} из {role['group_size']}"
    )
    if not duty:
        return f"{place}. Отдельного описания роли в группе нет."
    return f"{place}: {duty}."


def load_rules(path: Path) -> list[SourceDoc]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return split_rules(text, source_id=str(path.as_posix()))


def load_history(
    *,
    project_db: Path | None = None,
    documents: list[dict[str, Any]] | None = None,
) -> list[SourceDoc]:
    docs: list[SourceDoc] = []
    if documents:
        docs.extend(_docs_from_payloads(documents))
    if project_db is not None and project_db.is_file():
        docs.extend(_history_from_sqlite(project_db))
    return docs


def _docs_from_payloads(documents: list[dict[str, Any]]) -> list[SourceDoc]:
    result: list[SourceDoc] = []
    for raw in documents:
        source_id = str(raw.get("source_id") or raw.get("task_id") or "")
        if not source_id:
            continue
        title = str(raw.get("title") or source_id)
        text = str(raw.get("text") or "")
        messages = raw.get("messages")
        metadata = {
            "node_key": raw.get("node_key"),
            "version": raw.get("version"),
            "project_id": raw.get("project_id"),
            "updated_at": raw.get("updated_at"),
            "kind": raw.get("kind") or "task",
        }
        if isinstance(messages, list) and messages:
            windows = window_messages(messages, size=10)
            for index, window in enumerate(windows):
                rendered = _render_messages(window)
                parts = split_history_text(rendered) or [rendered]
                for part_index, part in enumerate(parts):
                    result.append(
                        SourceDoc(
                            corpus="history",
                            source_id=source_id,
                            title=title,
                            text=part,
                            part_key=f"w{index}-p{part_index}",
                            metadata=metadata,
                        )
                    )
            continue
        parts = split_history_text(text)
        for part_index, part in enumerate(parts):
            result.append(
                SourceDoc(
                    corpus="history",
                    source_id=source_id,
                    title=title,
                    text=part,
                    part_key=f"p{part_index}",
                    metadata=metadata,
                )
            )
    return result


def _render_messages(messages: list[dict[str, Any]]) -> str:
    lines = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "").strip()
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _history_from_sqlite(db_path: Path) -> list[SourceDoc]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        docs: list[SourceDoc] = []
        if "projects" in tables:
            docs.extend(_projects(conn))
        if "orch_tasks" in tables:
            docs.extend(_tasks(conn))
        if "orch_messages" in tables:
            docs.extend(_chats(conn))
        return docs
    finally:
        conn.close()


def _cell(row: sqlite3.Row, cols: set[str], name: str, default: Any = "") -> Any:
    if name not in cols:
        return default
    value = row[name]
    return default if value is None else value


def _colnames(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _projects(conn: sqlite3.Connection) -> list[SourceDoc]:
    cols = _colnames(conn, "projects")
    wanted = [name for name in ("id", "slug", "title", "topic", "status", "updated_at") if name in cols]
    if "id" not in wanted:
        return []
    rows = conn.execute(f"SELECT {', '.join(wanted)} FROM projects").fetchall()
    docs: list[SourceDoc] = []
    for row in rows:
        project_id = int(row["id"])
        title = str(_cell(row, cols, "title") or _cell(row, cols, "slug") or project_id)
        slug = _cell(row, cols, "slug")
        topic = _cell(row, cols, "topic")
        status = _cell(row, cols, "status")
        updated_raw = _cell(row, cols, "updated_at", default=None)
        updated = str(updated_raw) if updated_raw not in (None, "") else None
        text = (
            f"Проект {project_id} slug {slug}. Название: {title}. "
            f"Тема: {topic}. Статус: {status}. Обновлён: {updated or '—'}."
        )
        docs.append(
            SourceDoc(
                corpus="history",
                source_id=f"project:{project_id}",
                title=f"Проект {title}",
                text=text,
                part_key="full",
                metadata={
                    "node_key": None,
                    "version": None,
                    "project_id": project_id,
                    "updated_at": updated,
                    "kind": "project",
                },
            )
        )
    return docs


def _tasks(conn: sqlite3.Connection) -> list[SourceDoc]:
    cols = _colnames(conn, "orch_tasks")
    if "id" not in cols:
        return []
    rows = conn.execute("SELECT * FROM orch_tasks").fetchall()
    docs: list[SourceDoc] = []
    for row in rows:
        task_id = str(row["id"])
        title = str(row["title"] or task_id) if "title" in cols else task_id
        status = str(row["status"] or "") if "status" in cols else ""
        summary = ""
        if "summary" in cols and row["summary"]:
            summary = str(row["summary"])
        elif "result" in cols and row["result"]:
            summary = str(row["result"])
        project_id = (
            int(row["project_id"]) if "project_id" in cols and row["project_id"] is not None else None
        )
        updated = str(row["updated_at"]) if "updated_at" in cols and row["updated_at"] is not None else None
        text = f"Задача {task_id}. {title}. Статус: {status}. {summary}".strip()
        for index, part in enumerate(split_history_text(text) or [text]):
            docs.append(
                SourceDoc(
                    corpus="history",
                    source_id=f"task:{task_id}",
                    title=title,
                    text=part,
                    part_key=f"p{index}",
                    metadata={
                        "node_key": None,
                        "version": None,
                        "project_id": project_id,
                        "updated_at": updated,
                        "kind": "task",
                    },
                )
            )
    return docs


def _chats(conn: sqlite3.Connection) -> list[SourceDoc]:
    cols = _colnames(conn, "orch_messages")
    if not {"role", "content"} <= cols:
        return []
    session_col = "session_id" if "session_id" in cols else ("task_id" if "task_id" in cols else None)
    if session_col is None:
        return []
    order_col = "id" if "id" in cols else session_col
    rows = conn.execute(f"SELECT * FROM orch_messages ORDER BY {order_col}").fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        session = str(row[session_col])
        grouped.setdefault(session, []).append({"role": row["role"], "content": row["content"]})
    payloads = []
    for session, messages in grouped.items():
        project_id = None
        payloads.append(
            {
                "source_id": f"chat:{session}",
                "title": f"Чат {session}",
                "messages": messages,
                "project_id": project_id,
                "kind": "chat",
                "updated_at": None,
            }
        )
    return _docs_from_payloads(payloads)


def load_nodes_and_prompts(
    *,
    repo_root: Path,
    node_store: MemoryNodeStore | SqliteNodeStore | None,
    scan_git: bool,
) -> tuple[list[SourceDoc], list[SourceDoc], int]:
    """Возвращает (nodes, prompts, skipped_invalid_keys)."""
    from app.orchestrator.rag.node_api import reset_skipped_nodes, skipped_node_keys

    invalidate_group_role_cache()
    reset_skipped_nodes()
    merged: dict[str, ResolvedNode] = {}
    skipped = 0
    if scan_git:
        for node in iter_git_nodes(repo_root):
            if not NODE_KEY_RE.match(node.node_key):
                skipped += 1
                continue
            merged[node.node_key] = node
    store = node_store or MemoryNodeStore()
    for override in store.list_overrides(templates_root=repo_root / "templates" / "node_groups"):
        if not NODE_KEY_RE.match(override.node_key):
            skipped += 1
            continue
        if override.status == "disabled":
            merged.pop(override.node_key, None)
            continue
        base = merged.get(override.node_key)
        merged[override.node_key] = merge_resolved(base, override)
    node_docs: list[SourceDoc] = []
    prompt_docs: list[SourceDoc] = []
    for node in merged.values():
        if node.status == "disabled":
            continue
        node_docs.append(_node_doc(node))
        prompt_docs.extend(_prompt_docs(node))
    skipped += len(skipped_node_keys())
    return node_docs, prompt_docs, skipped


def _git_node(
    *,
    node_key: str,
    group_key: str,
    title: str,
    prompt_template: str,
    version: int,
    params: dict[str, Any] | None = None,
    description: str = "",
    prompt_path: str | None = None,
) -> ResolvedNode:
    return ResolvedNode(
        node_key=node_key,
        group_key=group_key,
        title=title,
        status="active",
        prompt_template=prompt_template,
        source="git",
        version=version,
        db_version=None,
        git_sha256=git_content_sha256(prompt_template),
        stale_base=False,
        params=params or {},
        description=description,
        prompt_path=prompt_path,
    )


def _node_metadata(node: ResolvedNode, *, prompt_path: str | None = None) -> dict[str, Any]:
    """Поля Chunk.metadata плюс разрешённый NodeDef. Без active_version / overridden_in_db."""
    meta: dict[str, Any] = {
        "node_key": node.node_key,
        "version": node.version,
        "project_id": None,
        "updated_at": node.updated_at,
        "source": node.source,
        "db_version": node.db_version,
        "git_sha256": node.git_sha256,
        "stale_base": node.stale_base,
        "group_key": node.group_key,
        "status": node.status,
        "updated_change_id": node.updated_change_id,
        "title": node.title,
    }
    role = group_role_index().get(node.node_key)
    if role:
        meta["group_role"] = role["group_role"]
        meta["group_order"] = role["group_order"]
        meta["group_size"] = role["group_size"]
        meta["group_title"] = role["group_title"]
        meta["role_source"] = role["role_source"]
    if prompt_path:
        meta["prompt_path"] = prompt_path
    return meta


def _prompt_lead(template: str) -> str:
    body = (template or "").strip()
    if not body:
        return ""
    paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
    lead = " ".join(paragraphs[:2])
    return lead[:500]


def _node_doc(node: ResolvedNode) -> SourceDoc:
    glued = node.node_key.replace("_", "")
    params = json.dumps(node.params, ensure_ascii=False, sort_keys=True) if node.params else "{}"
    lead = _prompt_lead(node.prompt_template)
    phrase = _role_phrase(node)
    text = (
        f"Нода {node.node_key}. Ключ {glued}. Группа {node.group_key}. "
        f"Название: {node.title}. {phrase} Статус: {node.status}. "
        f"Версия: {node.version}. Источник: {node.source}. "
        f"Описание: {node.description}. Параметры: {params}. "
        f"О чём промпт: {lead}"
    )
    return SourceDoc(
        corpus="nodes",
        source_id=node.node_key,
        title=node.title,
        text=text,
        part_key="catalog",
        metadata=_node_metadata(node),
    )


def _prompt_docs(node: ResolvedNode) -> list[SourceDoc]:
    template = (node.prompt_template or "").strip()
    if not template:
        return []
    header = f"Промпт «{node.title}» ноды {node.node_key}, версия {node.version}."
    phrase = _role_phrase(node)
    if phrase:
        header = f"{header} {phrase}"
    # До 8 частей по ##. Склейка в 3 ведра снята: секция не должна тонуть в чужом тексте.
    parts = split_prompt(template, max_parts=8)
    docs: list[SourceDoc] = []
    for part_key, body in parts:
        docs.append(
            SourceDoc(
                corpus="prompts",
                source_id=node.node_key,
                title=f"{node.title} · промпт",
                text=f"{header}\n{body}",
                part_key=part_key,
                metadata=_node_metadata(node, prompt_path=node.prompt_path),
            )
        )
    return docs


def _front_matter_version(meta: dict[str, Any]) -> int:
    """Нечисловой version не роняет reindex: берём 1 и считаем такой случай."""
    global _version_fallbacks
    raw = meta.get("version")
    if raw in (None, ""):
        return 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        _version_fallbacks += 1
        logger.warning("нечисловой version %r в шаблоне ноды, берём 1", raw)
        return 1


def iter_git_nodes(repo_root: Path) -> Iterable[ResolvedNode]:
    # Сначала код (NODE_GROUPS), затем файлы templates/node_groups — файл важнее.
    yield from _nodes_from_builtin(repo_root)
    yield from _nodes_from_template_dir(repo_root / "templates" / "node_groups")


def _nodes_from_template_dir(root: Path) -> Iterable[ResolvedNode]:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in {".md", ".json"} or not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            yield from _nodes_from_group_json(path)
            continue
        text = path.read_text(encoding="utf-8")
        meta, body = _split_front_matter(text)
        node_key = str(meta.get("node_key") or _key_from_stem(path.stem))
        group_key = str(meta.get("group_key") or path.parent.name)
        title = str(meta.get("title") or path.stem)
        version = _front_matter_version(meta)
        yield _git_node(
            node_key=node_key,
            group_key=group_key,
            title=title,
            prompt_template=body,
            version=version,
            params=_parse_params_field(meta.get("params")),
            description=str(meta.get("description") or ""),
            prompt_path=str(path.as_posix()),
        )


def _nodes_from_group_json(path: Path) -> Iterable[ResolvedNode]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    group_key = str(raw.get("group_id") or raw.get("group_key") or path.stem)
    nodes = raw.get("nodes") or []
    if not isinstance(nodes, list):
        return
    for item in nodes:
        if not isinstance(item, dict):
            continue
        node_key = str(item.get("preferred_id") or item.get("node_key") or "")
        if not node_key:
            continue
        prompt_template = str(item.get("prompt_template") or "")
        prompt_path = None
        variant = item.get("prompt_variant") or item.get("prompt_file")
        if not prompt_template and variant:
            found = _resolve_prompt_file(_repo_root_from_template(path), str(variant))
            if found is not None:
                prompt_template = found.read_text(encoding="utf-8")
                prompt_path = str(found.as_posix())
        yield _git_node(
            node_key=node_key,
            group_key=group_key,
            title=str(item.get("label") or item.get("title") or node_key),
            prompt_template=prompt_template,
            version=1,
            params=item.get("params") if isinstance(item.get("params"), dict) else {},
            description=str(item.get("description") or ""),
            prompt_path=prompt_path,
        )


def _nodes_from_builtin(repo_root: Path) -> Iterable[ResolvedNode]:
    try:
        from app.services.node_groups import NODE_GROUPS
    except Exception:
        return
    for group in NODE_GROUPS.values():
        for spec in group.nodes:
            prompt_path = None
            prompt_template = ""
            if spec.prompt_variant:
                found = _resolve_prompt_file(repo_root, spec.prompt_variant)
                if found is not None:
                    prompt_template = found.read_text(encoding="utf-8")
                    prompt_path = str(found.as_posix())
            yield _git_node(
                node_key=spec.preferred_id,
                group_key=group.group_id,
                title=spec.label,
                prompt_template=prompt_template,
                version=1,
                description=spec.description,
                prompt_path=prompt_path,
            )


def _resolve_prompt_file(repo_root: Path, variant: str) -> Path | None:
    names = [variant]
    if variant.startswith("sd_"):
        names.append(variant.removeprefix("sd_"))
    candidates: list[Path] = []
    node_groups = repo_root / "templates" / "node_groups"
    agents = repo_root / "templates" / "excel_gpt_agents"
    scene = repo_root / "prompts" / "scene_design"
    for name in names:
        candidates.append(agents / f"{name}.md")
        candidates.append(scene / f"{name}.md")
        if node_groups.is_dir():
            candidates.extend(node_groups.rglob(f"{name}.md"))
    for path in candidates:
        if path.is_file():
            return path
    return None


def _repo_root_from_template(path: Path) -> Path:
    for parent in path.parents:
        if parent.name == "templates":
            return parent.parent
    return path.parent


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, text[match.end() :]


def _parse_params_field(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _key_from_stem(stem: str) -> str:
    if NODE_KEY_RE.match(stem):
        return stem
    slug = re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_")
    return f"n_{slug}" if slug else "n_unknown"
