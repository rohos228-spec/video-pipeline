"""Граф канваса проекта: nodes/edges/positions в project.meta.canvas_graph."""

from __future__ import annotations

import copy as _copy
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import NodeRun, NodeRunStatus, Project, Workflow, WorkflowRun, WorkflowRunStatus
from app.services.node_status_machine import reset_node_to_pending

# Поля data ноды канваса, которые относятся к runtime (статусы/результаты).
_CANVAS_NODE_DATA_STRIP_KEYS = frozenset(
    {
        "status",
        "progress",
        "progressText",
        "error",
        "attempts",
    }
)


def canvas_graph_from_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(meta, dict):
        return None
    raw = meta.get("canvas_graph")
    if not isinstance(raw, dict):
        return None
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    edges = raw.get("edges")
    if not isinstance(edges, list):
        edges = []
    return {"nodes": nodes, "edges": edges, "workflow_id": raw.get("workflow_id")}


def sanitize_canvas_graph_for_inherit(cg: dict[str, Any]) -> dict[str, Any]:
    """Копия canvas_graph без статусов/результатов на нодах (для подпроектов)."""
    out = _copy.deepcopy(cg)
    nodes = out.get("nodes")
    if not isinstance(nodes, list):
        return out
    clean_nodes: list[dict[str, Any]] = []
    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        node = _copy.deepcopy(raw)
        data = node.get("data")
        if isinstance(data, dict):
            node["data"] = {
                k: v for k, v in data.items() if k not in _CANVAS_NODE_DATA_STRIP_KEYS
            }
        clean_nodes.append(node)
    out["nodes"] = clean_nodes
    return out


def build_canvas_graph_payload(
    *,
    workflow_id: int,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "nodes": nodes,
        "edges": edges,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


async def sync_run_snapshot_from_canvas_graph(
    session: AsyncSession,
    project: Project,
) -> bool:
    """Копирует canvas_graph проекта в WorkflowRun.nodes_snapshot (для graph planner)."""
    cg = canvas_graph_from_meta(project.meta if isinstance(project.meta, dict) else {})
    if not cg:
        return False
    run = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project.id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one_or_none()
    if run is None:
        return False
    nodes = list(cg["nodes"])
    edges = list(cg["edges"])
    run.nodes_snapshot = nodes
    run.edges_snapshot = edges
    node_ids = {str(n.get("id")) for n in nodes if n.get("id")}
    existing = {nr.node_key for nr in run.node_runs}
    for n in nodes:
        nid = n.get("id")
        if not nid or nid in existing:
            continue
        session.add(
            NodeRun(
                workflow_run_id=run.id,
                node_key=str(nid),
                node_type=str(n.get("type") or ""),
            )
        )
    for nr in list(run.node_runs):
        if nr.node_key not in node_ids:
            await session.delete(nr)
    await session.flush()
    logger.debug(
        "canvas_graph: synced run #{} snapshot ({} nodes, {} edges)",
        run.id,
        len(nodes),
        len(edges),
    )
    return True


async def ensure_subproject_workflow_run(
    session: AsyncSession,
    project: Project,
    *,
    workflow_id: int | None = None,
) -> WorkflowRun | None:
    """WorkflowRun + NodeRun'ы из canvas_graph; все ноды в pending."""
    cg = canvas_graph_from_meta(project.meta if isinstance(project.meta, dict) else {})
    wf_id = workflow_id
    if cg and cg.get("workflow_id") is not None:
        try:
            wf_id = int(cg["workflow_id"])
        except (TypeError, ValueError):
            pass
    if wf_id is None:
        wf = (
            await session.execute(
                select(Workflow).where(Workflow.is_default == True)  # noqa: E712
            )
        ).scalar_one_or_none()
        if wf is None:
            return None
        wf_id = wf.id

    wf = await session.get(Workflow, wf_id)
    if wf is None:
        return None

    run = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project.id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one_or_none()

    nodes = list(cg["nodes"]) if cg else list(wf.nodes or [])
    edges = list(cg["edges"]) if cg else list(wf.edges or [])

    if run is None:
        run = WorkflowRun(
            workflow_id=wf.id,
            project_id=project.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=nodes,
            edges_snapshot=edges,
        )
        session.add(run)
        await session.flush()
        for node in nodes:
            nid = node.get("id")
            if not nid:
                continue
            session.add(
                NodeRun(
                    workflow_run_id=run.id,
                    node_key=str(nid),
                    node_type=str(node.get("type") or ""),
                    status=NodeRunStatus.pending,
                )
            )
        await session.flush()
        return run

    await sync_run_snapshot_from_canvas_graph(session, project)
    await session.refresh(run, attribute_names=["node_runs"])
    for nr in run.node_runs:
        if nr.status is not NodeRunStatus.pending:
            reset_node_to_pending(
                nr, project_id=project.id, initiator="batch_inherit"
            )
    await session.flush()
    return run
