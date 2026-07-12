"""Тесты: граф канваса в project.meta.canvas_graph."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow, WorkflowRun
from app.orchestrator.graph.planner import WorkflowGraph, load_graph_for_project
from app.services.canvas_graph import (
    build_canvas_graph_payload,
    canvas_graph_from_meta,
    sync_run_snapshot_from_canvas_graph,
)


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_canvas_graph_from_meta_valid():
    meta = {
        "canvas_graph": {
            "workflow_id": 1,
            "nodes": [{"id": "plan", "type": "plan", "position": {"x": 0, "y": 0}}],
            "edges": [],
        }
    }
    cg = canvas_graph_from_meta(meta)
    assert cg is not None
    assert len(cg["nodes"]) == 1
    assert cg["workflow_id"] == 1


def test_canvas_graph_from_meta_empty_nodes():
    assert canvas_graph_from_meta({"canvas_graph": {"nodes": []}}) is None


def test_build_canvas_graph_payload_has_saved_at():
    payload = build_canvas_graph_payload(
        workflow_id=2,
        nodes=[{"id": "a", "type": "plan", "position": {"x": 1, "y": 2}}],
        edges=[],
    )
    assert payload["workflow_id"] == 2
    assert "saved_at" in payload


@pytest.mark.asyncio
async def test_load_graph_prefers_project_canvas(session: AsyncSession) -> None:
    project = Project(slug="cg", topic="Canvas", status=ProjectStatus.new)
    session.add(project)
    await session.flush()
    nodes = [
        {"id": "custom_plan", "type": "plan", "position": {"x": 99, "y": 88}},
        {"id": "custom_script", "type": "script", "position": {"x": 200, "y": 88}},
    ]
    edges = [{"id": "e1", "source": "custom_plan", "target": "custom_script"}]
    project.meta = {
        "canvas_graph": build_canvas_graph_payload(
            workflow_id=1, nodes=nodes, edges=edges
        )
    }
    graph = await load_graph_for_project(session, project)
    assert isinstance(graph, WorkflowGraph)
    assert len(graph.nodes) == 2
    assert graph.nodes[0]["id"] == "custom_plan"


@pytest.mark.asyncio
async def test_sync_run_snapshot_from_canvas_graph(session: AsyncSession) -> None:
    wf = Workflow(
        name="default",
        is_default=True,
        nodes=[{"id": "plan", "type": "plan", "position": {"x": 0, "y": 0}}],
        edges=[],
    )
    session.add(wf)
    project = Project(slug="sync", topic="Sync", status=ProjectStatus.new)
    session.add(project)
    await session.flush()
    nodes = [{"id": "n1", "type": "plan", "position": {"x": 0, "y": 0}}]
    project.meta = {
        "canvas_graph": build_canvas_graph_payload(
            workflow_id=wf.id, nodes=nodes, edges=[]
        )
    }
    run = WorkflowRun(workflow_id=wf.id, project_id=project.id, nodes_snapshot=[], edges_snapshot=[])
    session.add(run)
    await session.flush()
    changed = await sync_run_snapshot_from_canvas_graph(session, project)
    assert changed is True
    await session.refresh(run)
    assert len(run.nodes_snapshot or []) == 1
    assert run.nodes_snapshot[0]["id"] == "n1"
