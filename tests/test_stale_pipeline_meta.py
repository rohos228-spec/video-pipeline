"""Stale frames / enrich_completed_slots не должны пропускать split и excel_gpt #1."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    Frame,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.orchestrator.auto_advance import TRANSITIONS, _apply_approve
from app.orchestrator.graph.planner import WorkflowGraph
from app.services.project_state import (
    clear_stale_downstream_meta,
    compute_actual_status,
    recompute_status,
)


@pytest.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _frames(session, project_id: int, n: int = 3) -> None:
    for i in range(1, n + 1):
        session.add(
            Frame(
                project_id=project_id,
                number=i,
                voiceover_text=f"voiceover text for frame {i}",
            )
        )


@pytest.mark.asyncio
async def test_stale_enrich_meta_does_not_promote_script_ready(session) -> None:
    p = Project(
        slug="stale-enrich",
        topic="t",
        status=ProjectStatus.script_ready,
        general_plan="x" * 200,
        script_text="script text here",
        meta={"enrich_completed_slots": [1, 2, 3]},
    )
    session.add(p)
    await session.flush()
    _frames(session, p.id)
    await session.flush()

    cleared = clear_stale_downstream_meta(p)
    assert "enrich_completed_slots" in cleared
    actual = await compute_actual_status(session, p)
    assert actual is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_recompute_does_not_jump_to_enrich_then_slot3(session) -> None:
    p = Project(
        slug="stale-recompute",
        topic="t",
        status=ProjectStatus.script_ready,
        general_plan="x" * 200,
        script_text="script text here",
        auto_mode=True,
        meta={"enrich_completed_slots": [1, 2]},
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    _frames(session, p.id)
    await session.flush()

    old, new, changed = await recompute_status(session, p)
    assert new is ProjectStatus.script_ready
    assert p.status is ProjectStatus.script_ready
    await _apply_approve(
        session, p, None, TRANSITIONS[ProjectStatus.script_ready], bot=None
    )
    assert p.status is ProjectStatus.splitting


@pytest.mark.asyncio
async def test_split_noderun_pending_blocks_frames_ready_promotion(session) -> None:
    wf = Workflow(name="default", is_default=True, nodes=[], edges=[])
    session.add(wf)
    await session.flush()
    p = Project(
        slug="stale-frames",
        topic="t",
        status=ProjectStatus.script_ready,
        general_plan="x" * 200,
        script_text="script text here",
        meta={},
    )
    session.add(p)
    await session.flush()
    _frames(session, p.id)
    run = WorkflowRun(
        workflow_id=wf.id,
        project_id=p.id,
        status=WorkflowRunStatus.new,
        nodes_snapshot=[{"id": "n_split", "type": "split"}],
        edges_snapshot=[],
    )
    session.add(run)
    await session.flush()
    session.add(
        NodeRun(
            workflow_run_id=run.id,
            node_key="n_split",
            node_type="split",
            status=NodeRunStatus.pending,
        )
    )
    await session.flush()

    actual = await compute_actual_status(session, p)
    assert actual is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_stale_split_noderun_done_does_not_skip_to_frames_ready(session) -> None:
    """Как #42: после plan split NodeRun=done + кадры → НЕ frames_ready без meta."""
    wf = Workflow(name="default", is_default=True, nodes=[], edges=[])
    session.add(wf)
    await session.flush()
    p = Project(
        slug="stale-split-done",
        topic="t",
        status=ProjectStatus.plan_ready,
        general_plan="x" * 200,
        script_text="script text here",
        meta={},  # split_completed сброшен при рестарте plan
    )
    session.add(p)
    await session.flush()
    _frames(session, p.id)
    run = WorkflowRun(
        workflow_id=wf.id,
        project_id=p.id,
        status=WorkflowRunStatus.new,
        nodes_snapshot=[
            {"id": "n_script", "type": "script"},
            {"id": "n_split", "type": "split"},
        ],
        edges_snapshot=[],
    )
    session.add(run)
    await session.flush()
    for key, typ in (("n_script", "script"), ("n_split", "split")):
        session.add(
            NodeRun(
                workflow_run_id=run.id,
                node_key=key,
                node_type=typ,
                status=NodeRunStatus.done,
            )
        )
    await session.flush()

    actual = await compute_actual_status(session, p)
    assert actual is ProjectStatus.script_ready

    old, new, changed = await recompute_status(session, p, log_prefix="recompute(web_get)")
    assert old is ProjectStatus.plan_ready
    assert new is ProjectStatus.plan_ready
    assert changed is False
    assert p.status is ProjectStatus.plan_ready


@pytest.mark.asyncio
async def test_frames_ready_next_excel_gpt_is_slot1_not_slot3() -> None:
    nodes = [
        {"id": "n_split", "type": "split", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "n_eg1",
            "type": "excel_gpt",
            "position": {"x": 100, "y": 0},
            "data": {"slotIndex": 1},
        },
        {
            "id": "n_eg2",
            "type": "excel_gpt",
            "position": {"x": 200, "y": 0},
            "data": {"slotIndex": 2},
        },
        {
            "id": "n_eg3",
            "type": "excel_gpt",
            "position": {"x": 300, "y": 0},
            "data": {"slotIndex": 3},
        },
    ]
    edges = [
        {"id": "e1", "source": "n_split", "target": "n_eg1", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e2", "source": "n_eg1", "target": "n_eg2", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e3", "source": "n_eg2", "target": "n_eg3", "sourceHandle": "out", "targetHandle": "in"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        meta={},
    )
    assert g.next_running_after_ready(p, ProjectStatus.frames_ready) is (
        ProjectStatus.enriching_1
    )


@pytest.mark.asyncio
async def test_completed_excel_gpt_slots_skip_to_next_incomplete() -> None:
    nodes = [
        {"id": "n_split", "type": "split", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "n_eg1",
            "type": "excel_gpt",
            "position": {"x": 100, "y": 0},
            "data": {"slotIndex": 1},
        },
        {
            "id": "n_eg2",
            "type": "excel_gpt",
            "position": {"x": 200, "y": 0},
            "data": {"slotIndex": 2},
        },
        {
            "id": "n_eg3",
            "type": "excel_gpt",
            "position": {"x": 300, "y": 0},
            "data": {"slotIndex": 3},
        },
    ]
    edges = [
        {"id": "e1", "source": "n_split", "target": "n_eg1", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e2", "source": "n_eg1", "target": "n_eg2", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e3", "source": "n_eg2", "target": "n_eg3", "sourceHandle": "out", "targetHandle": "in"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        meta={"enrich_completed_slots": [1, 2], "split_completed": True},
    )
    # После реального split + завершения 1–2 — следующий incomplete = slot 3.
    assert g.next_running_after_ready(p, ProjectStatus.frames_ready) is (
        ProjectStatus.enriching_3
    )


@pytest.mark.asyncio
async def test_stale_enrich_meta_without_split_completed_starts_slot1() -> None:
    nodes = [
        {"id": "n_split", "type": "split", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "n_eg1",
            "type": "excel_gpt",
            "position": {"x": 100, "y": 0},
            "data": {"slotIndex": 1},
        },
        {
            "id": "n_eg2",
            "type": "excel_gpt",
            "position": {"x": 200, "y": 0},
            "data": {"slotIndex": 2},
        },
        {
            "id": "n_eg3",
            "type": "excel_gpt",
            "position": {"x": 300, "y": 0},
            "data": {"slotIndex": 3},
        },
    ]
    edges = [
        {"id": "e1", "source": "n_split", "target": "n_eg1", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e2", "source": "n_eg1", "target": "n_eg2", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e3", "source": "n_eg2", "target": "n_eg3", "sourceHandle": "out", "targetHandle": "in"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        meta={"enrich_completed_slots": [1, 2]},  # stale, no split_completed
    )
    assert g.next_running_after_ready(p, ProjectStatus.frames_ready) is (
        ProjectStatus.enriching_1
    )
