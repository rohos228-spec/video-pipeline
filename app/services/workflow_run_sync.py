"""Синхронизация WorkflowRun snapshot с шаблоном Workflow."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import NodeRun, Workflow, WorkflowRun


async def sync_runs_from_workflow(session: AsyncSession, workflow: Workflow) -> int:
    """Обновить nodes/edges snapshot у всех Run этого workflow. Возвращает число Run."""
    runs = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_id == workflow.id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalars().all()
    nodes = list(workflow.nodes or [])
    edges = list(workflow.edges or [])
    node_ids = {n["id"] for n in nodes if "id" in n}
    updated = 0
    for run in runs:
        run.nodes_snapshot = nodes
        run.edges_snapshot = edges
        existing_keys = {nr.node_key for nr in run.node_runs}
        for n in nodes:
            nid = n.get("id")
            if not nid or nid in existing_keys:
                continue
            session.add(
                NodeRun(
                    workflow_run_id=run.id,
                    node_key=nid,
                    node_type=str(n.get("type") or ""),
                )
            )
        for nr in list(run.node_runs):
            if nr.node_key not in node_ids:
                await session.delete(nr)
        updated += 1
    await session.flush()
    return updated
