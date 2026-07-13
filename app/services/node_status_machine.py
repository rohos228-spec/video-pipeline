"""Единственная точка смены NodeRun.status (строгая машина состояний).

Разрешённые переходы:
  pending → queued | skipped
  queued → running
  running → done | failed | waiting_hitl
  waiting_hitl → done | running | failed
  failed → queued
  любой → pending — только явный сброс/стоп пользователем (ui_reset, api_reset, api_stop)
  pending → done — только sync_checkpoint (upstream-ноды по чекпоинту проекта)

Запрещено:
  done минуя running (кроме sync_checkpoint для upstream)
  автоматические откаты назад
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models import NodeRun, NodeRunStatus

STATUS_LOG_PATH = Path("logs/status.log")

_FORWARD_TRANSITIONS: dict[NodeRunStatus, frozenset[NodeRunStatus]] = {
    NodeRunStatus.pending: frozenset({NodeRunStatus.queued, NodeRunStatus.skipped}),
    NodeRunStatus.queued: frozenset({NodeRunStatus.running}),
    NodeRunStatus.running: frozenset(
        {NodeRunStatus.done, NodeRunStatus.failed, NodeRunStatus.waiting_hitl}
    ),
    NodeRunStatus.waiting_hitl: frozenset(
        {NodeRunStatus.done, NodeRunStatus.running, NodeRunStatus.failed}
    ),
    NodeRunStatus.failed: frozenset({NodeRunStatus.queued}),
    NodeRunStatus.done: frozenset(),
    NodeRunStatus.skipped: frozenset(),
}

_RESET_INITIATORS = frozenset({"ui_reset", "api_reset", "api_stop"})
_SYNC_INITIATORS = frozenset({"sync", "sync_checkpoint", "worker"})


def _ensure_log_dir() -> None:
    STATUS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _write_status_log(line: str) -> None:
    try:
        _ensure_log_dir()
        with STATUS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning("node_status_machine: cannot write {}: {}", STATUS_LOG_PATH, e)


def _log_line(
    *,
    node_key: str,
    node_type: str,
    old: NodeRunStatus,
    new: NodeRunStatus,
    initiator: str,
    project_id: int | None,
    blocked: bool = False,
) -> None:
    ts = datetime.utcnow().isoformat(timespec="seconds")
    pid = project_id if project_id is not None else "?"
    action = "BLOCKED" if blocked else "OK"
    line = (
        f"{ts}\t{action}\tproject={pid}\tnode={node_type}/{node_key}\t"
        f"{old.value} → {new.value}\tinitiator={initiator}"
    )
    _write_status_log(line)


def _apply_side_effects(
    nr: NodeRun,
    old: NodeRunStatus,
    new: NodeRunStatus,
    *,
    initiator: str,
    error: str | None,
) -> None:
    now = datetime.utcnow()
    nr.status = new

    if initiator in _RESET_INITIATORS:
        nr.finished_at = None
        nr.started_at = None
        nr.error = None
        nr.progress = 0
        nr.progress_text = None
        return

    if new == NodeRunStatus.queued:
        nr.progress = 0
        nr.progress_text = None

    if new == NodeRunStatus.running and nr.started_at is None:
        nr.started_at = now

    if new == NodeRunStatus.done and nr.finished_at is None:
        nr.finished_at = now

    if new == NodeRunStatus.failed:
        nr.error = (error or nr.error or "ошибка шага")[:2000]
        nr.finished_at = now

    if old == NodeRunStatus.running and new == NodeRunStatus.queued:
        nr.progress = 0
        nr.progress_text = None


def transition_node_status(
    nr: NodeRun,
    new_status: NodeRunStatus,
    *,
    initiator: str,
    project_id: int | None = None,
    error: str | None = None,
    allow_checkpoint_backfill: bool = False,
) -> bool:
    """Сменить статус ноды. True — переход применён, False — запрещён (статус не меняется)."""
    old = nr.status
    if old == new_status:
        return False

    if new_status == NodeRunStatus.pending and initiator in _RESET_INITIATORS:
        _apply_side_effects(nr, old, new_status, initiator=initiator, error=error)
        _log_line(
            node_key=nr.node_key,
            node_type=nr.node_type,
            old=old,
            new=new_status,
            initiator=initiator,
            project_id=project_id,
        )
        return True

    if (
        allow_checkpoint_backfill
        and initiator in _SYNC_INITIATORS
        and old == NodeRunStatus.pending
        and new_status == NodeRunStatus.done
    ):
        _apply_side_effects(nr, old, new_status, initiator=initiator, error=error)
        _log_line(
            node_key=nr.node_key,
            node_type=nr.node_type,
            old=old,
            new=new_status,
            initiator="sync_checkpoint",
            project_id=project_id,
        )
        return True

    allowed = _FORWARD_TRANSITIONS.get(old, frozenset())
    if new_status not in allowed:
        logger.error(
            "node_status_machine: forbidden {} → {} for {}/{} (initiator={})",
            old.value,
            new_status.value,
            nr.node_type,
            nr.node_key,
            initiator,
        )
        _log_line(
            node_key=nr.node_key,
            node_type=nr.node_type,
            old=old,
            new=new_status,
            initiator=initiator,
            project_id=project_id,
            blocked=True,
        )
        return False

    _apply_side_effects(nr, old, new_status, initiator=initiator, error=error)
    _log_line(
        node_key=nr.node_key,
        node_type=nr.node_type,
        old=old,
        new=new_status,
        initiator=initiator,
        project_id=project_id,
    )
    return True


def apply_sync_target(
    nr: NodeRun,
    target: NodeRunStatus,
    *,
    project_id: int | None = None,
    checkpoint_upstream: bool = False,
) -> bool:
    """Применить целевой статус из run_sync (с учётом checkpoint backfill)."""
    return transition_node_status(
        nr,
        target,
        initiator="sync_checkpoint" if checkpoint_upstream else "sync",
        project_id=project_id,
        allow_checkpoint_backfill=checkpoint_upstream,
    )


def queue_node_for_start(nr: NodeRun, *, project_id: int | None, initiator: str = "api") -> bool:
    """pending | failed → queued перед запуском шага."""
    if nr.status == NodeRunStatus.failed:
        return transition_node_status(
            nr, NodeRunStatus.queued, initiator=initiator, project_id=project_id
        )
    if nr.status == NodeRunStatus.pending:
        return transition_node_status(
            nr, NodeRunStatus.queued, initiator=initiator, project_id=project_id
        )
    if nr.status == NodeRunStatus.queued:
        return False
    if nr.status == NodeRunStatus.running:
        return False
    raise ValueError(
        f"нода {nr.node_type} в статусе «{nr.status.value}» — нельзя поставить в очередь"
    )


def start_node_running(nr: NodeRun, *, project_id: int | None, initiator: str = "api") -> bool:
    """queued → running (воркер взял шаг)."""
    if nr.status == NodeRunStatus.running:
        return False
    if nr.status == NodeRunStatus.queued:
        return transition_node_status(
            nr, NodeRunStatus.running, initiator=initiator, project_id=project_id
        )
    if nr.status in (NodeRunStatus.pending, NodeRunStatus.failed):
        if not queue_node_for_start(nr, project_id=project_id, initiator=initiator):
            pass
        return transition_node_status(
            nr, NodeRunStatus.running, initiator=initiator, project_id=project_id
        )
    raise ValueError(
        f"нода {nr.node_type} в статусе «{nr.status.value}» — нельзя запустить"
    )


def complete_node(nr: NodeRun, *, project_id: int | None, initiator: str = "worker") -> bool:
    """running | waiting_hitl → done после успешного шага."""
    if nr.status == NodeRunStatus.done:
        return False
    return transition_node_status(
        nr, NodeRunStatus.done, initiator=initiator, project_id=project_id
    )


def fail_node(
    nr: NodeRun,
    error: str,
    *,
    project_id: int | None,
    initiator: str = "worker",
) -> bool:
    """running → failed при неуспешном завершении."""
    if nr.status == NodeRunStatus.failed:
        nr.error = error[:2000]
        return False
    return transition_node_status(
        nr,
        NodeRunStatus.failed,
        initiator=initiator,
        project_id=project_id,
        error=error,
    )


def reset_node_to_pending(
    nr: NodeRun, *, project_id: int | None, initiator: str = "api_reset"
) -> bool:
    """Явный сброс ноды пользователем."""
    return transition_node_status(
        nr, NodeRunStatus.pending, initiator=initiator, project_id=project_id
    )


def is_transition_allowed(
    old: NodeRunStatus,
    new: NodeRunStatus,
    *,
    initiator: str,
    allow_checkpoint_backfill: bool = False,
) -> bool:
    """Проверка без записи (для тестов)."""
    if old == new:
        return True
    if new == NodeRunStatus.pending and initiator in _RESET_INITIATORS:
        return True
    if (
        allow_checkpoint_backfill
        and initiator in _SYNC_INITIATORS
        and old == NodeRunStatus.pending
        and new == NodeRunStatus.done
    ):
        return True
    return new in _FORWARD_TRANSITIONS.get(old, frozenset())
