"""Резолв ноды: git-шаблон или активная версия из БД поверх него."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.orchestrator.node_store._db import NodeNotFoundError, NodeStoreError, ensure_conn, validate_node_key
from app.orchestrator.node_store.contracts_shim import NodeDef, NodeSource
from app.orchestrator.node_store.git_baseline import GitBaseline, load_git_baseline
from app.orchestrator.node_store.params import parse_params_json


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
    revert) — git-шаблон: ``source='git'``, ``db_version=None``,
    ``stale_base=False``. Если указатель NULL, а файла шаблона нет,
    это ``NodeNotFoundError``, не пустая нода. ``version`` для git равен 0.

    ``status='disabled'`` резолв не меняет: в ``NodeDef`` нет поля
    включённости, промпт и params отдаются как у активной строки.
    """
    key = validate_node_key(node_key)
    ensure_conn(conn)
    baseline = load_git_baseline(key, templates_root)
    row = conn.execute(
        "SELECT active_version FROM node_defs WHERE node_key = ?",
        (key,),
    ).fetchone()
    if row is None or row[0] is None:
        if not baseline.found:
            raise NodeNotFoundError(f"нода {key} не найдена в git")
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
    params = parse_params_json(stored[1], node_key=key, version=version)
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
