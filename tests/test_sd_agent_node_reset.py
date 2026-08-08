"""Точечный сброс NodeRun веера scene_design (reset_nodes_from_step)."""

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
from app.services.run_sync import reset_nodes_from_step


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
        {"id": "n_sd_assemble", "type": "sd_assemble", "data": {}},
        {"id": "n_hero", "type": "hero", "data": {}},
    ]
    for agent in ("characters", "world", "style", "camera", "action"):
        nodes.append({
            "id": f"n_sd_agent_{agent}",
            "type": "sd_agent",
            "data": {"agent": agent},
        })
    return nodes


async def _mk_project(session: AsyncSession) -> Project:
    wf = Workflow(
        name=f"wf-{uuid.uuid4().hex[:8]}",
        is_default=True,
        nodes=_fanout_nodes(),
        edges=[],
    )
    session.add(wf)
    await session.flush()
    project = Project(
        slug=f"sd-{uuid.uuid4().hex[:8]}",
        topic="t",
        status=ProjectStatus.scene_design_ready,
        meta={},
    )
    session.add(project)
    await session.flush()
    run = WorkflowRun(
        project_id=project.id,
        workflow_id=wf.id,
        status=WorkflowRunStatus.running,
        nodes_snapshot=wf.nodes,
        edges_snapshot=[],
    )
    session.add(run)
    await session.flush()
    for n in wf.nodes:
        session.add(NodeRun(
            workflow_run_id=run.id,
            node_key=n["id"],
            node_type=n["type"],
            status=NodeRunStatus.done,
        ))
    await session.flush()
    return project


async def _statuses(session: AsyncSession, project: Project) -> dict[str, str]:
    from sqlalchemy import select

    run = (
        await session.execute(
            select(WorkflowRun).where(WorkflowRun.project_id == project.id)
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(NodeRun).where(NodeRun.workflow_run_id == run.id)
        )
    ).scalars().all()
    return {nr.node_key: nr.status for nr in rows}


@pytest.mark.asyncio
async def test_reset_sd_cam_only_camera_and_downstream(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        await reset_nodes_from_step(session, project.id, "sd_cam")
        st = await _statuses(session, project)

    assert st["n_sd_agent_camera"] == NodeRunStatus.pending
    assert st["n_sd_assemble"] == NodeRunStatus.pending
    assert st["n_hero"] == NodeRunStatus.pending
    for agent in ("characters", "world", "style", "action"):
        assert st[f"n_sd_agent_{agent}"] == NodeRunStatus.done, agent
    assert st["n_split"] == NodeRunStatus.done


@pytest.mark.asyncio
async def test_reset_scene_asm_keeps_agent_nodes(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        await reset_nodes_from_step(session, project.id, "scene_asm")
        st = await _statuses(session, project)

    assert st["n_sd_assemble"] == NodeRunStatus.pending
    assert st["n_hero"] == NodeRunStatus.pending
    for agent in ("characters", "world", "style", "camera", "action"):
        assert st[f"n_sd_agent_{agent}"] == NodeRunStatus.done, agent


@pytest.mark.asyncio
async def test_reset_scene_d_resets_whole_fanout(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        await reset_nodes_from_step(session, project.id, "scene_d")
        st = await _statuses(session, project)

    for agent in ("characters", "world", "style", "camera", "action"):
        assert st[f"n_sd_agent_{agent}"] == NodeRunStatus.pending, agent
    assert st["n_sd_assemble"] == NodeRunStatus.pending
    assert st["n_hero"] == NodeRunStatus.pending
    assert st["n_split"] == NodeRunStatus.done
