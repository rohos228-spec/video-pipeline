"""Резолв ноды: git-шаблон или активная версия из БД поверх него."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.orchestrator.node_store._db import NodeNotFoundError, NodeStoreError, ensure_conn, validate_node_key
from app.orchestrator.node_store.contracts_shim import NodeDef, NodeSource
from app.orchestrator.node_store.git_baseline import GitBaseline, load_git_baseline


def resolve_node(
    node_key: str,
    conn: sqlite3.Connection,
    *,
    templates_root: Path | None = None,
) -> NodeDef:
    """Собрать NodeDef.

    ``active_version IS NOT NULL`` — промпт и params из этой строки
    ``node_prompt_versions`` (полный снимок поверх git). ``source='db'``,
    ``db_version`` равен номеру версии. ``git_sha256`` — sha256 текущего
    файла шаблона. ``stale_base`` — ``base_git_sha256`` версии не совпал
    с текущим хешем.

    Иначе (строки нет или ``active_version`` NULL, в том числе после
    revert) — ``source='git'``, ``db_version=None``, ``stale_base=False``.
    ``version`` для git равен 0: в таблице версии нумеруются с 1.
    """
    key = validate_node_key(node_key)
    ensure_conn(conn)
    baseline = load_git_baseline(key, templates_root)
    row = conn.execute(
        "SELECT active_version FROM node_defs WHERE node_key = ?",
        (key,),
    ).fetchone()
    if row is None and not baseline.found:
        raise NodeNotFoundError(f"нода {key} не найдена ни в БД, ни в git")
    if row is None or row[0] is None:
        return _from_git(key, baseline)
    version = int(row[0])
    stored = conn.execute(
        """
        SELECT prompt_template, params_json, base_git_sha256, kind
        FROM node_prompt_versions
        WHERE node_key = ? AND version = ?
        """,
        (key, version),
    ).fetchone()
    if stored is None:
        raise NodeStoreError(f"active_version {version} ноды {key} не найден в истории")
    kind = stored[3]
    if kind == "revert_to_git":
        raise NodeStoreError("active_version не может указывать на revert_to_git")
    params = _load_params(stored[1], key, version)
    base_hash = stored[2]
    return NodeDef(
        node_key=key,
        prompt_template=stored[0],
        params=params,
        source=NodeSource.db,
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
    """Тот же контракт, что у resolve_node. Правило 9 читает ноду через него."""
    return resolve_node(node_key, conn, templates_root=templates_root)


def _from_git(node_key: str, baseline: GitBaseline) -> NodeDef:
    return NodeDef(
        node_key=node_key,
        prompt_template=baseline.prompt_template,
        params=dict(baseline.params),
        source=NodeSource.git,
        version=0,
        db_version=None,
        git_sha256=baseline.sha256,
        stale_base=False,
    )


def _load_params(params_json: str, node_key: str, version: int) -> dict[str, Any]:
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as exc:
        raise NodeStoreError(
            f"params_json ноды {node_key} версии {version} не JSON"
        ) from exc
    if not isinstance(params, dict):
        raise NodeStoreError(
            f"params_json ноды {node_key} версии {version} должен быть объектом"
        )
    return params
