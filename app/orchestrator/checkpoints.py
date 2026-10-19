"""Сохранение и загрузка чекпоинта задачи.

Снимок: план, позиция, change_id, spend, краткий контекст.
Статус задачи здесь не пишется — это делает только Supervisor.
"""

from __future__ import annotations

import json
import threading

from app.orchestrator.contracts import CheckpointData, PlanStep, SpendSnapshot
from app.orchestrator.db.migrations import connect, utc_now

# load+insert контекста не должен затереть change_id, который другая
# корутина уже записала новым снимком.
_LOCK = threading.RLock()


class CheckpointStore:
    def save(self, task_id: int, session_id: str, data: CheckpointData) -> int:
        with _LOCK:
            return self._insert(task_id, session_id, data)

    def save_context(self, task_id: int, session_id: str, context: str) -> None:
        """Дописать контекст в последний снимок, не откатывая план и change_id."""
        with _LOCK:
            data = self._load(task_id)
            if data is None or (data.context or "") == (context or ""):
                return
            self._insert(task_id, session_id, data.model_copy(update={"context": context or ""}))

    def _insert(self, task_id: int, session_id: str, data: CheckpointData) -> int:
        conn = connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO orch_checkpoints (
                    task_id, session_id, plan_json, position,
                    change_ids_json, spend_json, context, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    session_id,
                    json.dumps([step.model_dump() for step in data.plan], ensure_ascii=False),
                    int(data.position),
                    json.dumps(list(data.change_ids), ensure_ascii=False),
                    json.dumps(data.spend.model_dump(), ensure_ascii=False),
                    data.context or "",
                    utc_now(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()

    def load_latest(self, task_id: int) -> CheckpointData | None:
        with _LOCK:
            return self._load(task_id)

    def _load(self, task_id: int) -> CheckpointData | None:
        conn = connect()
        try:
            row = conn.execute(
                """
                SELECT plan_json, position, change_ids_json, spend_json, context
                FROM orch_checkpoints
                WHERE task_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        raw_plan = json.loads(row["plan_json"] or "[]")
        raw_ids = json.loads(row["change_ids_json"] or "[]")
        raw_spend = json.loads(row["spend_json"] or "{}")
        return CheckpointData(
            plan=[PlanStep.model_validate(item) for item in raw_plan],
            position=int(row["position"]),
            change_ids=[str(item) for item in raw_ids],
            spend=SpendSnapshot.model_validate(raw_spend),
            context=row["context"] or "",
        )
