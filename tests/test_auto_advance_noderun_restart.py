"""auto_advance должен переводить done-ноду в running (иначе UI не «в работе»)."""

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
from app.orchestrator.auto_advance import _prepare_node_run_for_status


@pytest.fixture
async def mem_db(monkeypatch):
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


@pytest.mark.asyncio
async def test_prepare_auto_advance_restarts_done_script_node(mem_db, monkeypatch) -> None:
    """Как в логе #42: script уже done → auto_advance scripting должен дать running."""
    async with mem_db() as session:
        wf = Workflow(name=f"wf-{uuid.uuid4().hex[:8]}", is_default=True, nodes=[], edges=[])
        session.add(wf)
        await session.flush()
        project = Project(
            slug=f"aa-nr-{uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.plan_ready,
        )
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=[{"id": "n_script", "type": "script"}],
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        nr = NodeRun(
            workflow_run_id=run.id,
            node_key="n_script",
            node_type="script",
            status=NodeRunStatus.done,
        )
        session.add(nr)
        await session.flush()
        pid, nrid, wfid = project.id, nr.id, wf.id

    async def _wf_id(_session=None) -> int:
        return wfid

    monkeypatch.setattr("app.services.run_sync._get_default_workflow_id", _wf_id)

    async with mem_db() as session:
        project = await session.get(Project, pid)
        assert project is not None
        # без allow_restart — как старый баг
        await _prepare_node_run_for_status(
            session, project, ProjectStatus.scripting, allow_restart=False
        )
        nr = await session.get(NodeRun, nrid)
        assert nr is not None
        assert nr.status == NodeRunStatus.done

        await _prepare_node_run_for_status(
            session, project, ProjectStatus.scripting, allow_restart=True
        )
        nr = await session.get(NodeRun, nrid)
        assert nr is not None
        assert nr.status == NodeRunStatus.running
