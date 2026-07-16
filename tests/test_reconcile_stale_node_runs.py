"""Startup reconcile for stale NodeRuns."""

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
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services.run_sync import reconcile_stale_node_runs_on_startup


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
async def test_stale_running_node_run_becomes_failed(mem_db) -> None:
    slug = f"stale-nr-{uuid.uuid4().hex[:8]}"
    async with mem_db() as session:
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
        await session.flush()
        nr_id = nr.id

    fixed = await reconcile_stale_node_runs_on_startup()
    assert fixed >= 1

    async with mem_db() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.failed
        assert "рабочий процесс не активен" in (row.error or "")
