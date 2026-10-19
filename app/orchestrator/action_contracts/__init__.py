"""Схемы результатов действий WS2. Исполнителей здесь нет.

Пакет не лежит в ``app.orchestrator.actions`` и не отдаёт ``ACTION_IDS``:
приёмка R0b ищет там живой реестр. Список 102 id — ``AUDIT_ACTION_IDS``
внутри ``contracts``, его не экспортируем.
"""

from app.orchestrator.action_contracts.contracts import (
    CONTRACT_VERSION,
    ActionMetadata,
    ActionResult,
    ApprovalRequired,
    EditResult,
    ReportResult,
    SoftDeleteResult,
    export_action_result_schema,
    metadata_of,
)

__all__ = [
    "CONTRACT_VERSION",
    "ActionMetadata",
    "ActionResult",
    "ApprovalRequired",
    "EditResult",
    "ReportResult",
    "SoftDeleteResult",
    "export_action_result_schema",
    "metadata_of",
]
