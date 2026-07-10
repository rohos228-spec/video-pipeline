"""Цель очереди генерации: полный проект или до выбранной ноды (включительно)."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.node_registry import (
    LINEAR_NODE_TYPES,
    NODE_TYPE_TO_READY,
    READY_TO_NODE_TYPE,
    RUNNING_TO_NODE_TYPE,
    WORK_NODES,
    is_work_node_type,
)

GenQueueRunMode = Literal["full", "until_node"]


def _meta_dict(project: Project) -> dict[str, Any]:
    meta = project.meta
    return meta if isinstance(meta, dict) else {}


def get_gen_queue_run(project: Project) -> dict[str, Any] | None:
    run = _meta_dict(project).get("gen_queue_run")
    return run if isinstance(run, dict) else None


def gen_queue_run_mode(project: Project) -> GenQueueRunMode:
    run = get_gen_queue_run(project)
    if not run:
        return "full"
    mode = run.get("mode")
    return "until_node" if mode == "until_node" else "full"


def is_gen_queue_run_complete(project: Project) -> bool:
    run = get_gen_queue_run(project)
    return bool(run and run.get("complete"))


def target_node_type(project: Project) -> str | None:
    run = get_gen_queue_run(project)
    if not run or run.get("mode") != "until_node":
        return None
    typ = run.get("target_node_type")
    return str(typ) if typ and is_work_node_type(str(typ)) else None


def _linear_index(node_type: str) -> int | None:
    try:
        return LINEAR_NODE_TYPES.index(node_type)
    except ValueError:
        return None


def _status_linear_index(status: ProjectStatus) -> int | None:
    if status in READY_TO_NODE_TYPE:
        return _linear_index(READY_TO_NODE_TYPE[status])
    if status in RUNNING_TO_NODE_TYPE:
        return _linear_index(RUNNING_TO_NODE_TYPE[status])
    if status is ProjectStatus.published:
        return len(LINEAR_NODE_TYPES) - 1
    if status is ProjectStatus.assembled:
        return _linear_index("assemble")
    return None


def status_at_or_past_target(project: Project, target_type: str) -> bool:
    """True если целевая нода уже выполнена (достигнут *_{ready})."""
    if target_type not in WORK_NODES:
        return False
    target_idx = _linear_index(target_type)
    if target_idx is None:
        return False
    spec = WORK_NODES[target_type]
    if project.status == spec.ready_status:
        return True
    cur_idx = _status_linear_index(project.status)
    if cur_idx is None:
        return False
    if project.status in RUNNING_TO_NODE_TYPE:
        return cur_idx > target_idx
    return cur_idx >= target_idx


def is_gen_queue_timeline_complete(project: Project) -> bool:
    """Завершён ли прогон проекта в рамках очереди (полный или до ноды)."""
    if is_gen_queue_run_complete(project):
        return True
    if gen_queue_run_mode(project) == "full":
        return False
    target = target_node_type(project)
    if not target:
        return False
    return status_at_or_past_target(project, target)


def ready_status_is_queue_target(
    project: Project, ready_status: ProjectStatus
) -> bool:
    """Текущий *_ready — это выбранная целевая нода очереди."""
    if gen_queue_run_mode(project) != "until_node":
        return False
    target = target_node_type(project)
    if not target:
        return False
    return NODE_TYPE_TO_READY.get(target) == ready_status


async def set_gen_queue_run(
    session: AsyncSession,
    project: Project,
    *,
    mode: GenQueueRunMode,
    target_node_key: str | None = None,
    target_node_type: str | None = None,
) -> None:
    meta = dict(_meta_dict(project))
    if mode == "full":
        meta["gen_queue_run"] = {"mode": "full", "complete": False}
    else:
        if not target_node_type or not is_work_node_type(target_node_type):
            raise ValueError(f"invalid target node type: {target_node_type!r}")
        meta["gen_queue_run"] = {
            "mode": "until_node",
            "target_node_key": target_node_key,
            "target_node_type": target_node_type,
            "complete": False,
        }
    project.meta = meta
    await session.flush()


async def clear_gen_queue_run(session: AsyncSession, project: Project) -> None:
    meta = dict(_meta_dict(project))
    if "gen_queue_run" in meta:
        del meta["gen_queue_run"]
        project.meta = meta
        await session.flush()


async def mark_gen_queue_run_complete(session: AsyncSession, project: Project) -> None:
    meta = dict(_meta_dict(project))
    run = meta.get("gen_queue_run")
    if not isinstance(run, dict):
        return
    run = dict(run)
    run["complete"] = True
    meta["gen_queue_run"] = run
    project.meta = meta
    await session.flush()
