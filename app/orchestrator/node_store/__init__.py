"""Node Store (D3): git-шаблон read-only и версии промпта в БД.

Публичный API:
- ``resolve_node`` / ``get_node`` → ``NodeDef``
- ``writer.create_node``
- ``writer.edit_prompt``
- ``writer.edit_params``
- ``writer.rollback_to_version``
- ``writer.revert_node_to_git``

Схему таблиц создаёт миграция WS3. Этот пакет её не применяет.
"""

from app.orchestrator.node_store import writer as writer
from app.orchestrator.node_store._db import NodeExistsError, NodeNotFoundError, NodeStoreError
from app.orchestrator.node_store.contracts_shim import NodeDef, NodeSource
from app.orchestrator.node_store.resolver import get_node, resolve_node
from app.orchestrator.node_store.writer import (
    create_node,
    edit_params,
    edit_prompt,
    revert_node_to_git,
    rollback_to_version,
)

__all__ = [
    "NodeDef",
    "NodeExistsError",
    "NodeNotFoundError",
    "NodeSource",
    "NodeStoreError",
    "create_node",
    "edit_params",
    "edit_prompt",
    "get_node",
    "resolve_node",
    "revert_node_to_git",
    "rollback_to_version",
    "writer",
]
