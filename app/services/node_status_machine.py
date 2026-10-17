"""Единственная точка смены NodeRun.status (строгая машина состояний).

Разрешённые переходы:
  pending → queued | skipped
  queued → running
  running → done | failed | waiting_hitl | waiting_approval
  waiting_hitl → done | running | failed
  waiting_approval → queued | running | failed | pending
  failed → queued
  любой → pending — явный сброс/стоп (ui_reset, api_reset, api_stop, ui_restart, auto_unstick)

Запрещено:
  done минуя running (только complete_node с initiator=worker;
    исключение: heal_success — failed→done когда Project уже доказывает успех)
  автоматические откаты назад (кроме явных reset-initiators)
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from app.models import NodeRun, NodeRunStatus
from app.services.disk_guard import append_rotating
from app.settings import settings

STATUS_LOG_PATH = Path("logs/status.log")
STATUS_SNAPSHOT_PATH = Path("logs/status.snapshot.log")

_FORWARD_TRANSITIONS: dict[NodeRunStatus, frozenset[NodeRunStatus]] = {
    NodeRunStatus.pending: frozenset({NodeRunStatus.queued, NodeRunStatus.skipped}),
    NodeRunStatus.queued: frozenset({NodeRunStatus.running}),
    NodeRunStatus.running: frozenset(
        {
            NodeRunStatus.done,
            NodeRunStatus.failed,
            NodeRunStatus.waiting_hitl,
            NodeRunStatus.waiting_approval,
        }
    ),
    NodeRunStatus.waiting_hitl: frozenset(
        {NodeRunStatus.done, NodeRunStatus.running, NodeRunStatus.failed}
    ),
    NodeRunStatus.waiting_approval: frozenset(
        {
            NodeRunStatus.queued,
            NodeRunStatus.running,
            NodeRunStatus.failed,
            NodeRunStatus.pending,
        }
    ),
    NodeRunStatus.failed: frozenset({NodeRunStatus.queued}),
    NodeRunStatus.done: frozenset(),
    NodeRunStatus.skipped: frozenset(),
}

_RESET_INITIATORS = frozenset(
    {
        "ui_reset",
        "api_reset",
        "api_stop",
        "ui_restart",
        "auto_unstick",
        # Точечный ▶ scene_design: чужие sd_agent, ошибочно в running/queued,
        # вернуть в pending (иначе залипают → background_reconcile → failed).
        "only_agent_scope",
    }
)

# Contextvar: True только внутри transition_node_status / _apply_side_effects.
_status_machine_write: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "status_machine_write", default=False
)


def is_status_write_allowed() -> bool:
    return _status_machine_write.get()


@contextmanager
def allow_replica_status_write():
    """Копия NodeRun между state.db и project.db — не переход FSM."""
    token = _status_machine_write.set(True)
    try:
        yield
    finally:
        _status_machine_write.reset(token)


def guard_direct_status_write(nr: NodeRun, new_status: NodeRunStatus) -> None:
    if is_status_write_allowed():
        return
    msg = (
        f"BYPASS NodeRun.status write: {nr.node_type}/{nr.node_key} "
        f"→ {new_status.value} (use transition_node_status)"
    )
    if settings.node_status_strict:
        raise RuntimeError(msg)
    logger.error(msg)


def _transitions_log_enabled() -> bool:
    return (os.environ.get("VP_STATUS_LOG_TRANSITIONS") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _write_status_log(line: str) -> None:
    if not _transitions_log_enabled():
        return
    try:
        append_rotating(STATUS_LOG_PATH, line)
    except OSError as e:
        logger.warning("node_status_machine: cannot write {}: {}", STATUS_LOG_PATH, e)


def format_status_line(
    *,
    node_key: str,
    node_type: str,
    old: NodeRunStatus,
    new: NodeRunStatus,
    initiator: str,
    project_id: int | None,
    blocked: bool = False,
    ts: str | None = None,
) -> str:
    ts = ts or datetime.utcnow().isoformat(timespec="seconds")
    pid = project_id if project_id is not None else "?"
    action = "BLOCKED" if blocked else "OK"
    return (
        f"{ts}\t{action}\tproject={pid}\tnode={node_type}/{node_key}\t"
        f"{old.value} → {new.value}\tinitiator={initiator}"
    )


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
    _write_status_log(format_status_line(
        node_key=node_key, node_type=node_type, old=old, new=new, initiator=initiator,
        project_id=project_id, blocked=blocked,
    ))


def _row_ts(nr: Any) -> str:
    for attr in ("finished_at", "started_at", "created_at"):
        v = getattr(nr, attr, None)
        if v is not None:
            return v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else str(v)
    return datetime.utcnow().isoformat(timespec="seconds")


def derive_status_lines(rows: Iterable[tuple[Any, int | None]]) -> list[str]:
    out: list[str] = []
    for nr, project_id in rows:
        status = nr.status.value if hasattr(nr.status, "value") else str(nr.status)
        pid = project_id if project_id is not None else "?"
        line = (f"{_row_ts(nr)}\tSNAPSHOT\tproject={pid}\tnode={nr.node_type}/{nr.node_key}\t"
                f"{status}\tattempts={getattr(nr, 'attempts', 0) or 0}")
        err = (getattr(nr, "error", None) or "").strip()
        if status == "failed" and err:
            line += "\terror=" + err.replace("\n", " ").replace("\t", " ")[:200]
        out.append(line)
    out.sort()
    return out


async def rebuild_status_snapshot(session, *, project_id: int | None = None,
                                  path: Path | None = None) -> int:
    from sqlalchemy import select

    from app.models import WorkflowRun

    stmt = select(NodeRun, WorkflowRun.project_id).join(WorkflowRun, WorkflowRun.id == NodeRun.workflow_run_id)
    if project_id is not None:
        stmt = stmt.where(WorkflowRun.project_id == project_id)
    result = await session.execute(stmt)
    lines = derive_status_lines((nr, pid) for nr, pid in result.all())
    target = Path(path) if path is not None else STATUS_SNAPSHOT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, target)
    return len(lines)


def _apply_side_effects(
    nr: NodeRun,
    old: NodeRunStatus,
    new: NodeRunStatus,
    *,
    initiator: str,
    error: str | None,
) -> None:
    now = datetime.utcnow()
    nr.__dict__.setdefault("_observation_transitions", []).append({
        "from": old.value, "to": new.value, "initiator": initiator,
        "actor": "worker" if initiator == "worker" else (
            "operator_route" if initiator in {"api_stop", "ui_reset", "api_reset", "ui_restart"} else "runtime"),
        "human_identity": None,
    })
    token = _status_machine_write.set(True)
    try:
        nr.status = new
    finally:
        _status_machine_write.reset(token)

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

    if new == NodeRunStatus.done:
        nr.progress_text = None
        nr.error = None

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
) -> bool:
    """Сменить статус ноды. True — переход применён, False — запрещён."""
    old = nr.status
    if old == new_status:
        return False

    if new_status == NodeRunStatus.running:
        from app.services.actions_v1 import check_managed_start

        decision = check_managed_start(project_id=project_id, node_key=nr.node_key)
        if decision is not None and not decision.allowed:
            logger.warning(
                "actions-v1 blocked start project={} node={} reason={}",
                project_id, nr.node_key, decision.reason,
            )
            _log_line(
                node_key=nr.node_key,
                node_type=nr.node_type,
                old=old,
                new=new_status,
                initiator=f"actions_v1:{decision.reason}",
                project_id=project_id,
                blocked=True,
            )
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
        initiator == "heal_success"
        and new_status == NodeRunStatus.done
        and old == NodeRunStatus.failed
    ):
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
    if nr.status == NodeRunStatus.waiting_approval:
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
    from app.services.actions_v1 import check_managed_start

    decision = check_managed_start(project_id=project_id, node_key=nr.node_key)
    if decision is not None and not decision.allowed:
        logger.warning(
            "actions-v1 blocked start project={} node={} reason={}",
            project_id, nr.node_key, decision.reason,
        )
        _log_line(
            node_key=nr.node_key,
            node_type=nr.node_type,
            old=nr.status,
            new=NodeRunStatus.running,
            initiator=f"actions_v1:{decision.reason}",
            project_id=project_id,
            blocked=True,
        )
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
    if initiator != "worker":
        logger.error(
            "complete_node: initiator={!r} запрещён для {}/{}",
            initiator,
            nr.node_type,
            nr.node_key,
        )
        return False
    if nr.status == NodeRunStatus.done:
        return False
    return transition_node_status(
        nr, NodeRunStatus.done, initiator=initiator, project_id=project_id
    )


def heal_failed_node_done(
    nr: NodeRun, *, project_id: int | None
) -> bool:
    """failed → done когда Project.status уже доказывает успех шага."""
    if nr.status != NodeRunStatus.failed:
        return False
    return transition_node_status(
        nr,
        NodeRunStatus.done,
        initiator="heal_success",
        project_id=project_id,
    )


def sync_node_done_from_data(
    nr: NodeRun, *, project_id: int | None, initiator: str = "sidecar"
) -> bool:
    """Пометить ноду done, когда работа уже сделана вне официального шага.

    Sidecar (anim_pr) заполняет R48/DB без Project.status — canvas должен
    показать done, а не idle. Не трогает skipped.
    """
    if nr.status == NodeRunStatus.done:
        return False
    if nr.status == NodeRunStatus.skipped:
        return False
    if nr.status == NodeRunStatus.failed:
        return heal_failed_node_done(nr, project_id=project_id)
    if nr.status != NodeRunStatus.running:
        if not start_node_running(
            nr, project_id=project_id, initiator=initiator
        ):
            if nr.status != NodeRunStatus.running:
                return False
    return complete_node(nr, project_id=project_id, initiator="worker")


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
    """Явный сброс ноды пользователем или auto_unstick/ui_restart."""
    return transition_node_status(
        nr, NodeRunStatus.pending, initiator=initiator, project_id=project_id
    )


def mark_node_skipped(nr: NodeRun, *, project_id: int | None) -> bool:
    """pending → skipped (отключённая нода на канвасе)."""
    return transition_node_status(
        nr, NodeRunStatus.skipped, initiator="sync", project_id=project_id
    )


def is_transition_allowed(
    old: NodeRunStatus,
    new: NodeRunStatus,
    *,
    initiator: str,
) -> bool:
    """Проверка без записи (для тестов)."""
    if old == new:
        return True
    if new == NodeRunStatus.pending and initiator in _RESET_INITIATORS:
        return True
    return new in _FORWARD_TRANSITIONS.get(old, frozenset())
