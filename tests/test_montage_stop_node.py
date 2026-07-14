"""STOP сбрасывает зависшую assemble-ноду после фонового remount."""

from __future__ import annotations

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
)
from app.services.project_control import stop_project_running


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_stop_clears_stale_assemble_node(session: AsyncSession) -> None:
    """Проект audio_ready, assemble NodeRun running — STOP должен сбросить ноду."""
    wf = Workflow(name="default", is_default=True, nodes=[], edges=[])
    session.add(wf)
    p = Project(slug="t", topic="Test", status=ProjectStatus.audio_ready)
    session.add(p)
    await session.flush()

    run = WorkflowRun(
        workflow_id=wf.id,
        project_id=p.id,
        nodes_snapshot=[{"id": "n_assemble", "type": "assemble"}],
        edges_snapshot=[],
    )
    session.add(run)
    await session.flush()
    nr = NodeRun(
        workflow_run_id=run.id,
        node_key="n_assemble",
        node_type="assemble",
        status=NodeRunStatus.running,
        started_at=None,
    )
    session.add(nr)
    await session.flush()

    info = await stop_project_running(session, p)

    assert info["ok"] is True
    assert nr.status is NodeRunStatus.pending
    assert p.status is ProjectStatus.audio_ready
    assert (p.meta or {}).get("user_stop") is True


@pytest.mark.asyncio
async def test_stop_rolls_back_assembling_status(session: AsyncSession) -> None:
    wf = Workflow(name="default2", is_default=True, nodes=[], edges=[])
    session.add(wf)
    p = Project(slug="t2", topic="Test2", status=ProjectStatus.assembling)
    session.add(p)
    await session.flush()

    run = WorkflowRun(
        workflow_id=wf.id,
        project_id=p.id,
        nodes_snapshot=[{"id": "n_assemble", "type": "assemble"}],
        edges_snapshot=[],
    )
    session.add(run)
    await session.flush()
    nr = NodeRun(
        workflow_run_id=run.id,
        node_key="n_assemble",
        node_type="assemble",
        status=NodeRunStatus.running,
    )
    session.add(nr)
    await session.flush()

    info = await stop_project_running(session, p)

    assert info["ok"] is True
    assert info["stopped_kind"] == "running"
    assert nr.status is NodeRunStatus.pending
    assert p.status is ProjectStatus.audio_ready
