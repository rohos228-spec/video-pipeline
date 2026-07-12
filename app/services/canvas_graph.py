"""Граф канваса проекта: nodes/edges/positions в project.meta.canvas_graph."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import NodeRun, Project, WorkflowRun


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
