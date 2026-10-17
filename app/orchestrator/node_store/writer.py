"""Единственная точка записи node_defs и node_prompt_versions.

Порядок в одной транзакции (требование WS3, миграция 002):
сначала строка node_defs с active_version NULL, если ноды ещё нет;
затем INSERT версии; затем UPDATE указателя.
Указатель на kind=revert_to_git не ставится: revert пишет строку истории
и оставляет active_version NULL.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.orchestrator.node_store._db import (
    APPEND_MODES,
    PATCH_MODES,
    REPLACE_MODES,
    SECTION_MODES,
    NodeExistsError,
    NodeNotFoundError,
    NodeStoreError,
    atomic,
    utc_now,
    validate_change_id,
    validate_created_via,
    validate_group_key,
    validate_node_key,
    validate_origin,
    validate_reason,
    validate_title,
)
from app.orchestrator.node_store.git_baseline import GitBaseline, load_git_baseline

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

_KIND_CREATE = "create"
_KIND_EDIT_PROMPT = "edit_prompt"
_KIND_EDIT_PARAMS = "edit_params"
_KIND_ROLLBACK = "rollback"
_KIND_REVERT = "revert_to_git"


def create_node(
    conn: sqlite3.Connection,
    *,
    node_key: str,
    group_key: str,
    title: str,
    prompt_template: str,
    change_id: str,
    params: dict[str, Any] | None = None,
    after_node_key: str | None = None,
    is_new_group: bool = False,
    origin: str = "db",
    created_by: str | None = None,
    created_via: str = "orchestrator",
    reason: str | None = None,
    templates_root: Path | None = None,
) -> int:
    """Создать ноду и версию 1 (kind=create). Возвращает номер версии."""
    key = validate_node_key(node_key)
    group = validate_group_key(group_key)
    title_text = validate_title(title)
    change = validate_change_id(change_id)
    via = validate_created_via(created_via)
    origin_value = validate_origin(origin)
    reason_text = validate_reason(reason, required=False)
    if not isinstance(prompt_template, str):
        raise ValueError("prompt_template должен быть строкой")
    snapshot = _normalize_params(params if params is not None else _default_params())
    after = _optional_node_key(after_node_key)
    with atomic(conn):
        if _def_exists(conn, key):
            raise NodeExistsError(f"нода {key} уже есть")
        baseline = load_git_baseline(key, templates_root)
        stamp = utc_now()
        _insert_def(
            conn,
            node_key=key,
            group_key=group,
            title=title_text,
            origin=origin_value,
            is_new_group=is_new_group,
            after_node_key=after,
            change_id=change,
            created_by=created_by,
            stamp=stamp,
        )
        version = _insert_version(
            conn,
            node_key=key,
            prompt_template=prompt_template,
            params_json=_dump_params(snapshot),
            kind=_KIND_CREATE,
            patch_mode=None,
            section_anchor=None,
            reason=reason_text,
            parent_version=None,
            base_git_sha256=baseline.sha256,
            change_id=change,
            created_by=created_by,
            created_via=via,
            stamp=stamp,
        )
        _set_active_version(conn, key, version, change, stamp)
        return version


def edit_prompt(
    conn: sqlite3.Connection,
    *,
    node_key: str,
    content: str,
    patch_mode: str,
    change_id: str,
    reason: str,
    section_anchor: str | None = None,
    created_by: str | None = None,
    created_via: str = "orchestrator",
    group_key: str | None = None,
    title: str | None = None,
    templates_root: Path | None = None,
) -> int:
    """Новая версия kind=edit_prompt. При активной vN возвращает N+1.

    patch_mode: ``replace`` / ``replace_full`` заменяют промпт целиком.
    ``section`` / ``replace_section`` заменяют тело секции по заголовку
    ``section_anchor``. ``append_section`` дописывает в конец этой секции.
    """
    key = validate_node_key(node_key)
    change = validate_change_id(change_id)
    via = validate_created_via(created_via)
    reason_text = validate_reason(reason, required=True)
    if not isinstance(content, str):
        raise ValueError("content должен быть строкой")
    if patch_mode not in PATCH_MODES:
        raise ValueError(
            "patch_mode должен быть replace, section, replace_full, "
            "replace_section или append_section"
        )
    anchor = _clean_anchor(section_anchor)
    if patch_mode in SECTION_MODES | APPEND_MODES and not anchor:
        raise ValueError("section_anchor обязателен для section")
    if patch_mode in REPLACE_MODES:
        anchor = None
    with atomic(conn):
        state = _read_state(conn, key, templates_root)
        new_prompt = _apply_prompt_patch(state.prompt, patch_mode, content, anchor)
        stamp = utc_now()
        _ensure_def(
            conn,
            state,
            node_key=key,
            group_key=group_key,
            title=title,
            change_id=change,
            created_by=created_by,
            stamp=stamp,
        )
        version = _insert_version(
            conn,
            node_key=key,
            prompt_template=new_prompt,
            params_json=state.params_json,
            kind=_KIND_EDIT_PROMPT,
            patch_mode=patch_mode,
            section_anchor=anchor,
            reason=reason_text,
            parent_version=state.active_version,
            base_git_sha256=state.baseline.sha256,
            change_id=change,
            created_by=created_by,
            created_via=via,
            stamp=stamp,
        )
        _set_active_version(conn, key, version, change, stamp)
        return version


def edit_params(
    conn: sqlite3.Connection,
    *,
    node_key: str,
    params_patch: dict[str, Any],
    change_id: str,
    reason: str,
    created_by: str | None = None,
    created_via: str = "orchestrator",
    group_key: str | None = None,
    title: str | None = None,
    templates_root: Path | None = None,
) -> int:
    """Новая версия kind=edit_params: полный снимок params после shallow merge.

    Промпт копируется как есть. При активной vN возвращает N+1.
    """
    key = validate_node_key(node_key)
    change = validate_change_id(change_id)
    via = validate_created_via(created_via)
    reason_text = validate_reason(reason, required=True)
    if not isinstance(params_patch, dict):
        raise ValueError("params_patch должен быть объектом")
    patch = _normalize_params(params_patch)
    with atomic(conn):
        state = _read_state(conn, key, templates_root)
        merged = dict(state.params)
        merged.update(patch)
        _normalize_params(merged)
        stamp = utc_now()
        _ensure_def(
            conn,
            state,
            node_key=key,
            group_key=group_key,
            title=title,
            change_id=change,
            created_by=created_by,
            stamp=stamp,
        )
        version = _insert_version(
            conn,
            node_key=key,
            prompt_template=state.prompt,
            params_json=_dump_params(merged),
            kind=_KIND_EDIT_PARAMS,
            patch_mode=None,
            section_anchor=None,
            reason=reason_text,
            parent_version=state.active_version,
            base_git_sha256=state.baseline.sha256,
            change_id=change,
            created_by=created_by,
            created_via=via,
            stamp=stamp,
        )
        _set_active_version(conn, key, version, change, stamp)
        return version


def rollback_to_version(
    conn: sqlite3.Connection,
    *,
    node_key: str,
    version: int,
    change_id: str,
    reason: str,
    created_by: str | None = None,
    created_via: str = "orchestrator",
    templates_root: Path | None = None,
) -> int:
    """Новая строка kind=rollback с копией промпта и params указанной версии."""
    key = validate_node_key(node_key)
    change = validate_change_id(change_id)
    via = validate_created_via(created_via)
    reason_text = validate_reason(reason, required=True)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("version должен быть целым номером >= 1")
    with atomic(conn):
        if not _def_exists(conn, key):
            raise NodeNotFoundError(f"нода {key} не найдена")
        source = conn.execute(
            """
            SELECT prompt_template, params_json
            FROM node_prompt_versions
            WHERE node_key = ? AND version = ?
            """,
            (key, version),
        ).fetchone()
        if source is None:
            raise NodeStoreError(f"версия {version} ноды {key} не найдена")
        baseline = load_git_baseline(key, templates_root)
        stamp = utc_now()
        new_version = _insert_version(
            conn,
            node_key=key,
            prompt_template=source[0],
            params_json=source[1],
            kind=_KIND_ROLLBACK,
            patch_mode=None,
            section_anchor=None,
            reason=reason_text,
            parent_version=version,
            base_git_sha256=baseline.sha256,
            change_id=change,
            created_by=created_by,
            created_via=via,
            stamp=stamp,
        )
        _set_active_version(conn, key, new_version, change, stamp)
        return new_version


def revert_node_to_git(
    conn: sqlite3.Connection,
    *,
    node_key: str,
    change_id: str,
    reason: str,
    created_by: str | None = None,
    created_via: str = "orchestrator",
    templates_root: Path | None = None,
) -> int:
    """Строка kind=revert_to_git и active_version NULL.

    Возвращает номер строки истории. Этот номер нельзя записывать
    в active_version: триггер миграции такое UPDATE отвергает.
    Повторный revert, когда указатель и так NULL, ничего не пишет.
    """
    key = validate_node_key(node_key)
    change = validate_change_id(change_id)
    via = validate_created_via(created_via)
    reason_text = validate_reason(reason, required=True)
    with atomic(conn):
        row = conn.execute(
            "SELECT active_version FROM node_defs WHERE node_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise NodeNotFoundError(f"нода {key} не найдена")
        if row[0] is None:
            raise NodeStoreError(f"нода {key} уже на git-шаблоне")
        parent = int(row[0])
        baseline = load_git_baseline(key, templates_root)
        stamp = utc_now()
        version = _insert_version(
            conn,
            node_key=key,
            prompt_template=baseline.prompt_template,
            params_json=_dump_params(dict(baseline.params)),
            kind=_KIND_REVERT,
            patch_mode=None,
            section_anchor=None,
            reason=reason_text,
            parent_version=parent,
            base_git_sha256=baseline.sha256,
            change_id=change,
            created_by=created_by,
            created_via=via,
            stamp=stamp,
        )
        updated = conn.execute(
            """
            UPDATE node_defs
            SET active_version = NULL, updated_change_id = ?, updated_at = ?
            WHERE node_key = ?
            """,
            (change, stamp, key),
        )
        if updated.rowcount != 1:
            raise NodeStoreError(f"не удалось сбросить active_version ноды {key}")
        return version


class _State:
    def __init__(
        self,
        *,
        exists: bool,
        active_version: int | None,
        prompt: str,
        params: dict[str, Any],
        params_json: str,
        baseline: GitBaseline,
    ) -> None:
        self.exists = exists
        self.active_version = active_version
        self.prompt = prompt
        self.params = params
        self.params_json = params_json
        self.baseline = baseline


def _read_state(conn: sqlite3.Connection, node_key: str, templates_root: Path | None) -> _State:
    baseline = load_git_baseline(node_key, templates_root)
    row = conn.execute(
        "SELECT active_version FROM node_defs WHERE node_key = ?",
        (node_key,),
    ).fetchone()
    if row is None and not baseline.found:
        raise NodeNotFoundError(f"нода {node_key} не найдена ни в БД, ни в git")
    if row is None or row[0] is None:
        params = dict(baseline.params)
        return _State(
            exists=row is not None,
            active_version=None,
            prompt=baseline.prompt_template,
            params=params,
            params_json=_dump_params(params),
            baseline=baseline,
        )
    version = int(row[0])
    stored = conn.execute(
        """
        SELECT prompt_template, params_json, kind
        FROM node_prompt_versions
        WHERE node_key = ? AND version = ?
        """,
        (node_key, version),
    ).fetchone()
    if stored is None:
        raise NodeStoreError(f"active_version {version} ноды {node_key} не найден в истории")
    if stored[2] == _KIND_REVERT:
        raise NodeStoreError("active_version не может указывать на revert_to_git")
    params = json.loads(stored[1])
    if not isinstance(params, dict):
        raise NodeStoreError(f"params_json ноды {node_key} версии {version} должен быть объектом")
    return _State(
        exists=True,
        active_version=version,
        prompt=stored[0],
        params=params,
        params_json=stored[1],
        baseline=baseline,
    )


def _ensure_def(
    conn: sqlite3.Connection,
    state: _State,
    *,
    node_key: str,
    group_key: str | None,
    title: str | None,
    change_id: str,
    created_by: str | None,
    stamp: str,
) -> None:
    if state.exists:
        return
    group = _materialize_group(state, group_key)
    title_text = _materialize_title(state, title)
    _insert_def(
        conn,
        node_key=node_key,
        group_key=group,
        title=title_text,
        origin="git_override",
        is_new_group=False,
        after_node_key=None,
        change_id=change_id,
        created_by=created_by,
        stamp=stamp,
    )


def _materialize_group(state: _State, group_key: str | None) -> str:
    if group_key is not None:
        return validate_group_key(group_key)
    if state.baseline.group_key:
        return validate_group_key(state.baseline.group_key)
    raise ValueError("для первой записи git-ноды нужен group_key")


def _materialize_title(state: _State, title: str | None) -> str:
    if title is not None:
        return validate_title(title)
    if state.baseline.title:
        return validate_title(state.baseline.title)
    path = state.baseline.path
    if path is not None:
        return validate_title(path.stem)
    raise ValueError("для первой записи git-ноды нужен title")


def _def_exists(conn: sqlite3.Connection, node_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM node_defs WHERE node_key = ?",
        (node_key,),
    ).fetchone()
    return row is not None


def _insert_def(
    conn: sqlite3.Connection,
    *,
    node_key: str,
    group_key: str,
    title: str,
    origin: str,
    is_new_group: bool,
    after_node_key: str | None,
    change_id: str,
    created_by: str | None,
    stamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO node_defs (
            node_key, group_key, title, origin, is_new_group, after_node_key,
            status, active_version, created_change_id, updated_change_id,
            created_at, updated_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?, ?, ?, ?)
        """,
        (
            node_key,
            group_key,
            title,
            origin,
            1 if is_new_group else 0,
            after_node_key,
            change_id,
            change_id,
            stamp,
            stamp,
            created_by,
        ),
    )


def _insert_version(
    conn: sqlite3.Connection,
    *,
    node_key: str,
    prompt_template: str,
    params_json: str,
    kind: str,
    patch_mode: str | None,
    section_anchor: str | None,
    reason: str | None,
    parent_version: int | None,
    base_git_sha256: str | None,
    change_id: str,
    created_by: str | None,
    created_via: str,
    stamp: str,
) -> int:
    current = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM node_prompt_versions WHERE node_key = ?",
        (node_key,),
    ).fetchone()
    version = int(current[0]) + 1
    conn.execute(
        """
        INSERT INTO node_prompt_versions (
            node_key, version, prompt_template, params_json, kind,
            patch_mode, section_anchor, reason, parent_version,
            base_git_sha256, change_id, created_at, created_by, created_via
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_key,
            version,
            prompt_template,
            params_json,
            kind,
            patch_mode,
            section_anchor,
            reason,
            parent_version,
            base_git_sha256,
            change_id,
            stamp,
            created_by,
            created_via,
        ),
    )
    return version


def _set_active_version(
    conn: sqlite3.Connection,
    node_key: str,
    version: int,
    change_id: str,
    stamp: str,
) -> None:
    if version < 1:
        raise NodeStoreError("active_version начинается с 1")
    updated = conn.execute(
        """
        UPDATE node_defs
        SET active_version = ?, updated_change_id = ?, updated_at = ?
        WHERE node_key = ?
        """,
        (version, change_id, stamp, node_key),
    )
    if updated.rowcount != 1:
        raise NodeStoreError(f"не удалось обновить active_version ноды {node_key}")


def _apply_prompt_patch(
    prompt: str,
    patch_mode: str,
    content: str,
    section_anchor: str | None,
) -> str:
    if patch_mode in REPLACE_MODES:
        return content
    if not section_anchor:
        raise ValueError("section_anchor обязателен для section")
    lines = prompt.splitlines(keepends=True)
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        match = _HEADING.match(line.rstrip("\r\n"))
        if match is None:
            continue
        heading = match.group(2).strip()
        if heading == section_anchor or line.strip() == section_anchor:
            start = index
            level = len(match.group(1))
            break
    if start is None:
        raise ValueError(f"section_anchor не найден: {section_anchor}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = _HEADING.match(lines[index].rstrip("\r\n"))
        if match is not None and len(match.group(1)) <= level:
            end = index
            break
    heading_line = lines[start]
    if not heading_line.endswith("\n"):
        heading_line += "\n"
    if patch_mode in SECTION_MODES:
        body = _with_newline(content)
        return "".join(lines[:start]) + heading_line + body + "".join(lines[end:])
    if patch_mode in APPEND_MODES:
        body = "".join(lines[start + 1 : end])
        if body and not body.endswith("\n"):
            body += "\n"
        return "".join(lines[:start]) + heading_line + body + _with_newline(content) + "".join(lines[end:])
    raise ValueError(f"неизвестный patch_mode: {patch_mode}")


def _with_newline(content: str) -> str:
    if not content or content.endswith("\n"):
        return content
    return content + "\n"


def _default_params() -> dict[str, Any]:
    return {"model": "gpt-5.6-sol", "reasoning_effort": "high"}


def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(params, ensure_ascii=False, sort_keys=True)
        loaded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("params должны быть JSON-объектом") from exc
    if not isinstance(loaded, dict):
        raise ValueError("params должны быть объектом")
    return loaded


def _dump_params(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional_node_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return validate_node_key(text)


def _clean_anchor(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("section_anchor должен быть строкой")
    text = value.strip()
    return text or None
