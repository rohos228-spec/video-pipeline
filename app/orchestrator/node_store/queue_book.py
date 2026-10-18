"""Снимок очереди доски перед cancel_queue_ops.

Очистка очереди необратима для отложенной работы. Снимок пишется
до изменения списка, чтобы прежние операции можно было увидеть.
"""

from __future__ import annotations

import copy
from typing import Any


class QueueBook:
    """Очередь операций проекта и снимки перед снятием."""

    def __init__(self, ops: list[dict[str, Any]] | None = None) -> None:
        self.ops: list[dict[str, Any]] = [dict(op) for op in (ops or [])]
        self.snapshots: list[dict[str, Any]] = []

    def cancel_queue_ops(self, ids: set | None = None) -> dict:
        """Снимает выбранные операции или всю очередь. Сначала снимок."""
        self.snapshots.append({"before": copy.deepcopy(self.ops)})
        if ids is None:
            self.ops = []
        else:
            wanted = set(ids)
            self.ops = [op for op in self.ops if op.get("id") not in wanted]
        return {"snapshot_id": len(self.snapshots), "ops": list(self.ops)}
