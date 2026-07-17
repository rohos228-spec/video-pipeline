"""excel_gpt NodeRun должен становиться running при старте шага (UI SSoT)."""

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
from app.services.run_sync import (
    complete_active_node_for_step,
    prepare_node_for_step_start,
    update_active_node_progress_text,
)


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


async def _seed_excel_gpt_run(scope, *, keys: list[str] | None = None):
    keys = keys or ["n_excel_gpt_1", "n_excel_gpt_2"]
    slug = f"excel-nr-{uuid.uuid4().hex[:8]}"
    async with scope() as session:
        nodes = [
            {
                "id": k,
                "type": "excel_gpt",
                "position": {"x": i * 200, "y": 0},
                "data": {"slotIndex": i + 1, "label": "Работа с GPT"},
            }
            for i, k in enumerate(keys)
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
            slug=slug,
            topic="t",
            status=ProjectStatus.frames_ready,
            meta={
                "active_excel_gpt_node_key": keys[0],
                "canvas_graph": {"nodes": nodes, "edges": []},
            },
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
        nr_ids: dict[str, int] = {}
        for n in nodes:
            nr = NodeRun(
                workflow_run_id=run.id,
                node_key=n["id"],
                node_type="excel_gpt",
                status=NodeRunStatus.pending,
            )
            session.add(nr)
            await session.flush()
            nr_ids[n["id"]] = nr.id
        return project.id, wf.id, nr_ids


def _patch_default_workflow(monkeypatch, workflow_id: int) -> None:
    async def _wf_id(_session=None) -> int:
        return workflow_id

    monkeypatch.setattr("app.services.run_sync._get_default_workflow_id", _wf_id)


@pytest.mark.asyncio
async def test_prepare_marks_excel_gpt_node_running(mem_db, monkeypatch) -> None:
    project_id, wf_id, nr_ids = await _seed_excel_gpt_run(mem_db)
    _patch_default_workflow(monkeypatch, wf_id)
    key = "n_excel_gpt_1"

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        ok = await prepare_node_for_step_start(
            session,
            project,
            "excel_gpt",
            node_key=key,
            strict=True,
            explicit_ui_start=True,
        )
        assert ok is True

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids[key])
        other = await session.get(NodeRun, nr_ids["n_excel_gpt_2"])
        assert nr is not None and nr.status == NodeRunStatus.running
        assert other is not None and other.status == NodeRunStatus.pending


@pytest.mark.asyncio
async def test_start_step_excel_gpt_uses_active_key(mem_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    app_settings.settings.data_dir = tmp_path / "data"

    project_id, wf_id, nr_ids = await _seed_excel_gpt_run(mem_db)
    _patch_default_workflow(monkeypatch, wf_id)

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

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        status = await start_step(
            session,
            project,
            "excel_gpt",
            node_key="n_excel_gpt_2",
            skip_queue_guard=True,
            require_node_fsm=True,
            explicit_ui_start=True,
        )
        assert status is ProjectStatus.enriching_2

    async with mem_db() as session:
        nr2 = await session.get(NodeRun, nr_ids["n_excel_gpt_2"])
        nr1 = await session.get(NodeRun, nr_ids["n_excel_gpt_1"])
        assert nr2 is not None and nr2.status == NodeRunStatus.running
        assert nr1 is not None and nr1.status == NodeRunStatus.pending


@pytest.mark.asyncio
async def test_complete_and_progress_target_excel_gpt_not_enrich(
    mem_db, monkeypatch
) -> None:
    project_id, wf_id, nr_ids = await _seed_excel_gpt_run(mem_db)
    _patch_default_workflow(monkeypatch, wf_id)
    key = "n_excel_gpt_1"

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        await prepare_node_for_step_start(
            session,
            project,
            "excel_gpt",
            node_key=key,
            strict=True,
            explicit_ui_start=True,
        )
        project.status = ProjectStatus.enriching_1
        project.meta = {**(project.meta or {}), "active_excel_gpt_node_key": key}
        await update_active_node_progress_text(session, project, "ChatGPT: 1/3")
        nr_mid = await session.get(NodeRun, nr_ids[key])
        assert nr_mid is not None
        assert nr_mid.progress_text == "ChatGPT: 1/3"
        await complete_active_node_for_step(
            session,
            project,
            prev_status=ProjectStatus.enriching_1,
            new_status=ProjectStatus.enrich_1_ready,
        )

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids[key])
        assert nr is not None
        assert nr.status == NodeRunStatus.done
