"""Строки ``orch_tasks`` / ``orch_steps``. Статус меняет только Supervisor."""

from __future__ import annotations

import json
from typing import Any

from app.orchestrator.contracts import PlanStep, SpendSnapshot
from app.orchestrator.db.migrations import connect, utc_now


class TaskStore:
    def insert(
        self,
        session_id: str,
        goal: str,
        status: str,
        project_id: int | None = None,
    ) -> int:
        stamp = utc_now()
        conn = connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO orch_tasks (
                    session_id, status, goal, position,
                    spend_internal, spend_actions,
                    cancel_requested, pause_requested,
                    project_id, created_at, updated_at
                )
                VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?, ?, ?)
                """,
                (session_id, status, goal, project_id, stamp, stamp),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()

    def get(self, task_id: int) -> dict[str, Any] | None:
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM orch_tasks WHERE id = ?", (task_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return dict(row)

    def set_status(self, task_id: int, status: str, error: str | None = None) -> None:
        conn = connect()
        try:
            conn.execute(
                """
                UPDATE orch_tasks
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, utc_now(), task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def set_flag(
        self,
        task_id: int,
        *,
        cancel_requested: int | None = None,
        pause_requested: int | None = None,
        steer_text: str | None = None,
    ) -> None:
        sets: list[str] = []
        args: list[Any] = []
        if cancel_requested is not None:
            sets.append("cancel_requested = ?")
            args.append(int(cancel_requested))
        if pause_requested is not None:
            sets.append("pause_requested = ?")
            args.append(int(pause_requested))
        if steer_text is not None:
            sets.append("steer_text = ?")
            args.append(steer_text)
        if not sets:
            return
        sets.append("updated_at = ?")
        args.append(utc_now())
        args.append(task_id)
        conn = connect()
        try:
            conn.execute(f"UPDATE orch_tasks SET {', '.join(sets)} WHERE id = ?", args)
            conn.commit()
        finally:
            conn.close()

    def save_plan(self, task_id: int, plan: list[PlanStep], position: int | None = None) -> None:
        payload = json.dumps([step.model_dump() for step in plan], ensure_ascii=False)
        conn = connect()
        try:
            if position is None:
                conn.execute(
                    "UPDATE orch_tasks SET plan_json = ?, updated_at = ? WHERE id = ?",
                    (payload, utc_now(), task_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE orch_tasks
                    SET plan_json = ?, position = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (payload, int(position), utc_now(), task_id),
                )
            conn.commit()
        finally:
            conn.close()

    def mirror_progress(
        self,
        task_id: int,
        plan: list[PlanStep],
        position: int,
        spend: SpendSnapshot,
    ) -> None:
        conn = connect()
        try:
            conn.execute(
                """
                UPDATE orch_tasks
                SET plan_json = ?, position = ?, spend_internal = ?, spend_actions = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps([step.model_dump() for step in plan], ensure_ascii=False),
                    int(position),
                    float(spend.internal),
                    float(spend.actions),
                    utc_now(),
                    task_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def add_spend(self, task_id: int, *, internal: float = 0.0, actions: float = 0.0) -> SpendSnapshot:
        conn = connect()
        try:
            row = conn.execute(
                """
                UPDATE orch_tasks
                SET spend_internal = spend_internal + ?,
                    spend_actions = spend_actions + ?,
                    updated_at = ?
                WHERE id = ?
                RETURNING spend_internal, spend_actions
                """,
                (float(internal), float(actions), utc_now(), task_id),
            ).fetchone()
            if row is None:
                raise LookupError(task_id)
            conn.commit()
        finally:
            conn.close()
        return SpendSnapshot(
            internal=float(row["spend_internal"] or 0),
            actions=float(row["spend_actions"] or 0),
        )

    def insert_step(self, task_id: int, position: int, step: PlanStep) -> int:
        conn = connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO orch_steps (
                    task_id, position, action_name, params_json, expected,
                    paid, mutating, status, started_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    task_id,
                    position,
                    step.action,
                    json.dumps(step.params, ensure_ascii=False),
                    step.expected,
                    1 if step.paid else 0,
                    1 if step.mutating else 0,
                    utc_now(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()

    def note_running_outcome(self, step_id: int, change_id: str | None, cost: float) -> None:
        """Записать сдачу платного шага, не закрывая строку ``running``."""
        conn = connect()
        try:
            conn.execute(
                """
                UPDATE orch_steps
                SET change_id = ?, cost = ?
                WHERE id = ? AND status = 'running'
                """,
                (change_id, float(cost), step_id),
            )
            conn.commit()
        finally:
            conn.close()

    def finish_step(
        self,
        step_id: int,
        status: str,
        change_id: str | None,
        cost: float,
    ) -> None:
        conn = connect()
        try:
            conn.execute(
                """
                UPDATE orch_steps
                SET status = ?, change_id = ?, cost = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, change_id, float(cost), utc_now(), step_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_open(self) -> list[dict[str, Any]]:
        """Нетерминальные задачи. Порядок — по id, чтобы рестарт был стабильным."""
        conn = connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM orch_tasks
                WHERE status NOT IN ('done', 'failed', 'cancelled')
                ORDER BY id ASC
                """
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def list_steps(self, task_id: int) -> list[dict[str, Any]]:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT * FROM orch_steps WHERE task_id = ? ORDER BY position ASC",
                (task_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
