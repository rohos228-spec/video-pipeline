"""Ревью #352 п.8: failed не снимается авто-путём, только явным ▶."""

import inspect
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
from app.services import step_failure_policy
import app.main as main_mod


@pytest.fixture
async def mem():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as session:
            yield session
            await session.commit()

    yield scope
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_node_stays_failed_without_explicit_start(mem, monkeypatch) -> None:
    async with mem() as session:
        wf = Workflow(name="w", is_default=True, nodes=[{"id": "n1", "type": "excel_gpt"}], edges=[])
        session.add(wf)
        await session.flush()
        project = Project(slug=f"p-{uuid.uuid4().hex[:6]}", topic="t", status=ProjectStatus.frames_ready, meta={})
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=[{"id": "n1", "type": "excel_gpt"}],
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        nr = NodeRun(workflow_run_id=run.id, node_key="n1", node_type="excel_gpt", status=NodeRunStatus.failed)
        session.add(nr)
        await session.flush()
        pid, wid, nid = project.id, wf.id, nr.id

    async def _wf(_session=None):
        return wid

    monkeypatch.setattr("app.services.run_sync._get_default_workflow_id", _wf)
    monkeypatch.setattr("app.db.session_scope", mem)
    monkeypatch.setattr("app.services.run_sync.session_scope", mem)
    async with mem() as session:
        project = await session.get(Project, pid)
        ok = await prepare_node_for_step_start(
            session, project, "excel_gpt", node_key="n1", explicit_ui_start=False
        )
        assert ok is False
        row = await session.get(NodeRun, nid)
        assert row.status == NodeRunStatus.failed


def test_auto_paths_are_not_explicit_ui_start() -> None:
    policy = inspect.getsource(step_failure_policy.record_step_failure)
    assert "explicit_ui_start=False" in policy
    assert "explicit_ui_start=True" not in policy
    main_src = inspect.getsource(main_mod)
    chunk = main_src.split("fallback unstick")[0][-500:]
    assert "explicit_ui_start=False" in chunk
