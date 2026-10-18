"""Живой реестр для ядра: execute / metadata_of поверх ActionSpec.

Пакет схем — ``app.orchestrator.action_contracts``. Отсюда не экспортируется
``ACTION_IDS`` и ``AUDIT_ACTION_IDS``: белый список собирает адаптер из реестра.
"""

from app.orchestrator.actions.executor import execute, metadata_of, whitelist
from app.services.action_layer.spec import ensure_builtin_actions, registry

__all__ = [
    "ensure_builtin_actions",
    "execute",
    "metadata_of",
    "registry",
    "whitelist",
]
