"""Единственная точка замены чтения ноды на WS2 после мёржа PR #349.

Пока пакета ``app.orchestrator.node_store`` нет в main, берётся локальный
fallback с той же сигнатурой. Код WS2 сюда не копируется.

``NodeNotFoundError`` (и одноимённая ошибка другого модуля) не роняет
reindex: нода пропускается, пишется warning, ключ запоминается. Эти ключи
нельзя снимать как удалённые источники.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_skipped_keys: list[str] = []


def bind_resolve() -> tuple[Any, type[Exception], type[Exception]]:
    """``(resolve_node, NodeNotFoundError, NodeStoreError)``."""
    try:
        from app.orchestrator.node_store import NodeNotFoundError, NodeStoreError, resolve_node
    except ImportError:
        from app.orchestrator.rag.node_store import NodeNotFoundError, NodeStoreError, resolve_node

    return resolve_node, NodeNotFoundError, NodeStoreError


def reset_skipped_nodes() -> None:
    _skipped_keys.clear()


def skipped_node_keys() -> tuple[str, ...]:
    """Ключи, которые resolve пропустил с последнего ``reset_skipped_nodes``."""
    return tuple(_skipped_keys)


def resolve_node(
    node_key: str,
    conn: sqlite3.Connection,
    *,
    templates_root: Path | None = None,
) -> Any | None:
    """Снимок ноды. ``None`` — ноду надо пропустить, не удалять её чанки."""
    impl, missing, _broken = bind_resolve()
    try:
        return impl(node_key, conn, templates_root=templates_root)
    except Exception as exc:
        if not _is_missing_node(exc, missing):
            raise
        logger.warning("rag resolve: нода %s пропущена: %s", node_key, exc)
        if node_key not in _skipped_keys:
            _skipped_keys.append(node_key)
        return None


def _is_missing_node(exc: BaseException, missing: type[BaseException]) -> bool:
    """``NodeNotFoundError`` пакета WS2 или фолбэка, в том числе одноимённый класс."""
    return isinstance(exc, missing) or type(exc).__name__ == "NodeNotFoundError"


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
