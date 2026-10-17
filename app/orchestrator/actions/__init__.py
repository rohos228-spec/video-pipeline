"""Схемы результатов действий WS2. Исполнителей здесь нет.

Пакет не отдаёт ``ACTION_IDS`` и ``registry``: приёмка R0b ищет ими
живой реестр. Список 102 id остаётся внутри ``contracts`` и в метаданных.
"""

from app.orchestrator.actions.contracts import (
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
