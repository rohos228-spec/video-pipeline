"""Точечный ▶ sd_agent: prepare не зажигает весь веер в running."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services.run_sync import prepare_node_for_step_start
from app.services.scene_design import runner as sd_runner


@pytest.fixture
async def mem_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    monkeypatch.setattr("app.services.run_sync.session_scope", _scope)
    yield _scope
    await engine.dispose()


def _fanout_nodes() -> list[dict]:
    nodes = [
        {"id": "n_split", "type": "split", "data": {}},
        {
            "id": "n_excel_gpt_sd_cd_skeleton",
            "type": "excel_gpt",
            "data": {"sd_agent": "skeleton", "label": "скелет"},
        },
    ]
    for agent in ("characters", "world", "style", "action", "camera"):
        nodes.append(
            {
                "id": f"n_excel_gpt_sd_cd_{agent}",
                "type": "excel_gpt",
                "data": {"sd_agent": agent},
            }
        )
    nodes.append(
        {
            "id": "n_excel_gpt_sd_cd_asm",
            "type": "excel_gpt",
            "data": {"sd_agent": "assemble"},
        }
    )
    return nodes


@pytest.mark.asyncio
async def test_prepare_with_only_agent_does_not_fanout(mem_db) -> None:
    async with mem_db() as session:
        nodes = _fanout_nodes()
        wf = Workflow(
            name=f"wf-{uuid.uuid4().hex[:8]}",
            is_default=True,
            nodes=nodes,
            edges=[],
        )
        session.add(wf)
        await session.flush()
        project = Project(
            slug=f"oa-{uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.scene_designing,
            meta={
                "scene_design_enabled": True,
                "scene_design_skeleton": True,
                "canvas_graph": {
                    "workflow_id": wf.id,
                    "nodes": nodes,
                    "edges": [],
                },
            },
        )
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.running,
            nodes_snapshot=nodes,
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        for n in nodes:
            from app.services.excel_gpt_node import effective_node_type

            session.add(
                NodeRun(
                    workflow_run_id=run.id,
                    node_key=n["id"],
                    node_type=effective_node_type(n),
                    status=NodeRunStatus.pending,
                )
            )
        await session.flush()

        sd_runner.set_only_agent(project, "skeleton")
        await session.flush()

        # Как advance_project: prepare без node_key.
        ok = await prepare_node_for_step_start(
            session,
            project,
            "scene_d",
            node_key=None,
            strict=False,
            explicit_ui_start=True,
        )
        assert ok is True
        from sqlalchemy import select

        rows = (
            await session.scalars(
                select(NodeRun).where(NodeRun.workflow_run_id == run.id)
            )
        ).all()
        by_key = {nr.node_key: nr.status for nr in rows}
        assert by_key["n_excel_gpt_sd_cd_skeleton"] == NodeRunStatus.running
        for agent in ("characters", "world", "style", "action", "camera"):
            assert by_key[f"n_excel_gpt_sd_cd_{agent}"] == NodeRunStatus.pending, agent


@pytest.mark.asyncio
async def test_scene_d_wipe_must_not_drop_only_agent_before_prepare(mem_db) -> None:
    """Регресс: clear_step_outputs(_wipe_scene_design) убивал only_agent."""
    from app.services.reset_step import clear_step_outputs_for_rerun

    async with mem_db() as session:
        nodes = _fanout_nodes()
        wf = Workflow(
            name=f"wf-{uuid.uuid4().hex[:8]}",
            is_default=True,
            nodes=nodes,
            edges=[],
        )
        session.add(wf)
        await session.flush()
        project = Project(
            slug=f"oa-wipe-{uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.frames_ready,
            meta={
                "scene_design_enabled": True,
                "scene_design_skeleton": True,
                "scene_design": {"status": "agents_done", "only_agent": "skeleton"},
                "canvas_graph": {
                    "workflow_id": wf.id,
                    "nodes": nodes,
                    "edges": [],
                },
            },
        )
        session.add(project)
        await session.flush()

        sd_runner.set_only_agent(project, "skeleton")
        assert sd_runner.get_only_agent(project) == "skeleton"

        await clear_step_outputs_for_rerun(
            session, project, "scene_d", force_wipe=False
        )
        assert sd_runner.get_only_agent(project) == "skeleton"
