"""Единственная точка замены чтения ноды на WS2 после мёржа PR #349.

Пока пакета ``app.orchestrator.node_store`` нет в main, берётся локальный
fallback с той же сигнатурой. Код WS2 сюда не копируется.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def bind_resolve() -> tuple[Any, type[Exception], type[Exception]]:
    """``(resolve_node, NodeNotFoundError, NodeStoreError)``."""
    try:
        from app.orchestrator.node_store import NodeNotFoundError, NodeStoreError, resolve_node
    except ImportError:
        from app.orchestrator.rag.node_store import NodeNotFoundError, NodeStoreError, resolve_node

    return resolve_node, NodeNotFoundError, NodeStoreError


def resolve_node(
    node_key: str,
    conn: sqlite3.Connection,
    *,
    templates_root: Path | None = None,
) -> Any:
    impl, _missing, _broken = bind_resolve()
    return impl(node_key, conn, templates_root=templates_root)


def get_node(
    node_key: str,
    conn: sqlite3.Connection,
    *,
    templates_root: Path | None = None,
) -> Any:
    try:
        from app.orchestrator.node_store import get_node as impl
    except ImportError:
        from app.orchestrator.rag.node_store import get_node as impl

    return impl(node_key, conn, templates_root=templates_root)
