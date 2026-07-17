"""Ручной старт plan сбрасывает downstream NodeRuns → pending (последовательность)."""

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
from app.services.project_steps import start_step


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


@pytest.mark.asyncio
async def test_start_plan_resets_script_split_excel_to_pending(mem_db, monkeypatch) -> None:
    async with mem_db() as session:
        nodes = [
            {"id": "n_plan", "type": "plan"},
            {"id": "n_script", "type": "script"},
            {"id": "n_split", "type": "split"},
            {"id": "n_excel_gpt_1", "type": "excel_gpt"},
        ]
        wf = Workflow(
            name=f"wf-{uuid.uuid4().hex[:8]}",
            is_default=True,
            nodes=nodes,
            edges=[],
        )
        session.add(wf)
        await session.flush()
        project = Project(
            slug=f"seq-{uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.frames_ready,
            general_plan="x" * 200,
            script_text="script",
            meta={"split_completed": True, "enrich_completed_slots": [1]},
        )
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=nodes,
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        ids: dict[str, int] = {}
        for n in nodes:
            nr = NodeRun(
                workflow_run_id=run.id,
                node_key=n["id"],
                node_type=n["type"],
                status=NodeRunStatus.done,
            )
            session.add(nr)
            await session.flush()
            ids[n["id"]] = nr.id
        pid, wfid = project.id, wf.id

    async def _wf_id(_session=None) -> int:
        return wfid

    monkeypatch.setattr("app.services.run_sync._get_default_workflow_id", _wf_id)

    async def _noop_clear(*_a, **_k):
        return {}

    monkeypatch.setattr(
        "app.services.project_steps.clear_step_outputs_for_rerun",
        _noop_clear,
    )
    monkeypatch.setattr(
        "app.services.project_steps.purge_tmp_gpt_for_step",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.services.project_steps.sync_project_xlsx",
        _noop_clear,
    )

    async with mem_db() as session:
        project = await session.get(Project, pid)
        assert project is not None
        await start_step(
            session,
            project,
            "plan",
            skip_queue_guard=True,
            require_node_fsm=True,
            explicit_ui_start=True,
        )
        assert project.status is ProjectStatus.planning
        meta = project.meta if isinstance(project.meta, dict) else {}
        assert not meta.get("split_completed")

    async with mem_db() as session:
        plan = await session.get(NodeRun, ids["n_plan"])
        script = await session.get(NodeRun, ids["n_script"])
        split = await session.get(NodeRun, ids["n_split"])
        excel = await session.get(NodeRun, ids["n_excel_gpt_1"])
        assert plan is not None and plan.status == NodeRunStatus.running
        assert script is not None and script.status == NodeRunStatus.pending
        assert split is not None and split.status == NodeRunStatus.pending
        assert excel is not None and excel.status == NodeRunStatus.pending
