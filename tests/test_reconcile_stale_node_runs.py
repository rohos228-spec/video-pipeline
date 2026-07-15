"""Startup reconcile for stale NodeRuns."""

from __future__ import annotations

import pytest

from app.db import session_scope
from app.models import NodeRun, NodeRunStatus, Project, Workflow, WorkflowRun, WorkflowRunStatus
from app.services.run_sync import reconcile_stale_node_runs_on_startup


@pytest.mark.asyncio
async def test_stale_running_node_run_becomes_failed() -> None:
    import uuid

    slug = f"stale-nr-{uuid.uuid4().hex[:8]}"
    async with session_scope() as session:
        wf = Workflow(name="default", is_default=True, nodes=[], edges=[])
        session.add(wf)
        await session.flush()
        project = Project(slug=slug, topic="t", status="planning")
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.running,
        )
        session.add(run)
        await session.flush()
        nr = NodeRun(
            workflow_run_id=run.id,
            node_key="n_plan",
            node_type="plan",
            status=NodeRunStatus.running,
        )
        session.add(nr)
        await session.commit()
        nr_id = nr.id

    fixed = await reconcile_stale_node_runs_on_startup()
    assert fixed >= 1

    async with session_scope() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.failed
        assert "перезапуском" in (row.error or "")
