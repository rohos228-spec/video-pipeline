"""P0: NodeRun status machine as single source of truth."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.db import session_scope
from app.models import (
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services import batches as batches_svc
from app.services.project_meta import merge_project_meta
from app.services.project_steps import start_step
from app.services.run_sync import (
    _reconcile_stale_node_runs,
    prepare_node_for_step_start,
    sync_run_for_project,
)


async def _seed_project_with_run(
    *,
    project_status: ProjectStatus = ProjectStatus.plan_ready,
    node_status: NodeRunStatus = NodeRunStatus.pending,
    node_type: str = "plan",
    node_key: str = "n_plan",
) -> tuple[int, int, int]:
    """Returns (project_id, node_run_id, workflow_id)."""
    slug = f"nr-truth-{uuid.uuid4().hex[:8]}"
    async with session_scope() as session:
        wf = Workflow(name=f"wf-{uuid.uuid4().hex[:8]}", is_default=False, nodes=[], edges=[])
        session.add(wf)
        await session.flush()
        project = Project(slug=slug, topic="t", status=project_status)
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=[{"id": node_key, "type": node_type}],
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        nr = NodeRun(
            workflow_run_id=run.id,
            node_key=node_key,
            node_type=node_type,
            status=node_status,
        )
        session.add(nr)
        await session.flush()
        return project.id, nr.id, wf.id


def _patch_default_workflow(monkeypatch, workflow_id: int) -> None:
    async def _wf_id() -> int:
        return workflow_id

    monkeypatch.setattr(
        "app.services.run_sync._get_default_workflow_id",
        _wf_id,
    )


@pytest.mark.asyncio
async def test_run_sync_never_promotes_to_done() -> None:
    """D1: sync_run не переводит pending → done из project.status."""
    project_id, nr_id, _wf_id = await _seed_project_with_run(
        project_status=ProjectStatus.plan_ready,
        node_status=NodeRunStatus.pending,
    )

    await sync_run_for_project(project_id)

    async with session_scope() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.pending


@pytest.mark.asyncio
async def test_stale_running_reconciled_to_failed(monkeypatch) -> None:
    """D2: зависшая running без живой задачи → failed."""
    from app.services import step_cancel as sc

    project_id, nr_id, _wf_id = await _seed_project_with_run(
        project_status=ProjectStatus.planning,
        node_status=NodeRunStatus.running,
    )
    async with session_scope() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        nr.started_at = datetime.utcnow() - timedelta(seconds=60)
        await session.flush()

    monkeypatch.setattr(sc, "is_generation_active", lambda _pid: False)

    fixed = await _reconcile_stale_node_runs(
        initiator="background_reconcile",
        require_no_live_task=True,
        grace_sec=0,
    )
    assert fixed >= 1

    async with session_scope() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.failed
        assert "рабочий процесс не активен" in (row.error or "")


@pytest.mark.asyncio
async def test_prepare_auto_unstick_stale_running(monkeypatch) -> None:
    """D2: auto_unstick — запуск зависшей running из UI."""
    from app.services import step_cancel as sc

    project_id, nr_id, wf_id = await _seed_project_with_run(
        project_status=ProjectStatus.planning,
        node_status=NodeRunStatus.running,
    )
    _patch_default_workflow(monkeypatch, wf_id)
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.general_plan = "x" * 200

    monkeypatch.setattr(sc, "is_generation_active", lambda _pid: False)

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        ok = await prepare_node_for_step_start(
            session,
            project,
            "plan",
            node_key="n_plan",
            strict=True,
            explicit_ui_start=True,
        )
        assert ok is True

    async with session_scope() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        assert nr.status == NodeRunStatus.running


@pytest.mark.asyncio
async def test_ui_restart_done_node(monkeypatch) -> None:
    """D3: явный UI-запуск done-ноды → рестарт без ошибки."""
    project_id, nr_id, wf_id = await _seed_project_with_run(
        project_status=ProjectStatus.plan_ready,
        node_status=NodeRunStatus.done,
    )
    _patch_default_workflow(monkeypatch, wf_id)
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.general_plan = "x" * 200
        ok = await prepare_node_for_step_start(
            session,
            project,
            "plan",
            node_key="n_plan",
            strict=True,
            explicit_ui_start=True,
        )
        assert ok is True

    async with session_scope() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        assert nr.status == NodeRunStatus.running


@pytest.mark.asyncio
async def test_auto_start_rejects_done_without_ui_flag(monkeypatch) -> None:
    """D3: без explicit_ui_start done-нода не перезапускается."""
    project_id, nr_id, wf_id = await _seed_project_with_run(
        project_status=ProjectStatus.plan_ready,
        node_status=NodeRunStatus.done,
    )
    _patch_default_workflow(monkeypatch, wf_id)

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        ok = await prepare_node_for_step_start(
            session,
            project,
            "plan",
            node_key="n_plan",
            strict=False,
            explicit_ui_start=False,
        )
        assert ok is False

    async with session_scope() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        assert nr.status == NodeRunStatus.done


@pytest.mark.asyncio
async def test_start_step_passes_explicit_ui_restart(tmp_path, monkeypatch) -> None:
    """D3: start_step с explicit_ui_start перезапускает done."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")

    project_id, nr_id, wf_id = await _seed_project_with_run(
        project_status=ProjectStatus.plan_ready,
        node_status=NodeRunStatus.done,
        node_type="script",
        node_key="n_script",
    )
    _patch_default_workflow(monkeypatch, wf_id)
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.general_plan = "x" * 200
        project.script_text = "script"
        project.data_dir.mkdir(parents=True, exist_ok=True)
        (project.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
        status = await start_step(
            session,
            project,
            "script",
            node_key="n_script",
            skip_queue_guard=True,
            require_node_fsm=True,
            explicit_ui_start=True,
        )
        assert status is ProjectStatus.scripting

    async with session_scope() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        assert nr.status == NodeRunStatus.running


@pytest.mark.asyncio
async def test_merge_meta_preserves_prompts() -> None:
    """D4: merge meta не удаляет prompt-ключи."""
    existing = {
        "custom_prompts": {"n1": [{"id": "main"}]},
        "prompt_slot_variants": {"n1": {"main": "v"}},
        "prompt_history": {"n1": []},
    }
    merged = merge_project_meta(
        existing,
        {"canvas_graph": {"nodes": [], "edges": []}},
        source="test",
        project_id=1,
    )
    assert merged["custom_prompts"] == existing["custom_prompts"]
    assert merged["prompt_slot_variants"] == existing["prompt_slot_variants"]
    assert merged["prompt_history"] == existing["prompt_history"]
    assert "canvas_graph" in merged


@pytest.mark.asyncio
async def test_clean_subprojects_meta_keeps_prompts() -> None:
    """D4: clean_subprojects_meta не трогает промты."""
    from app.models import BatchProject

    async with session_scope() as session:
        batch = BatchProject(name="B", slug=f"b-p-{uuid.uuid4().hex[:6]}", status="new")
        session.add(batch)
        await session.flush()
        sub = Project(
            slug=f"{batch.slug}__001",
            topic="T",
            status=ProjectStatus.new,
            batch_id=batch.id,
            batch_position=1,
            batch_slug=batch.slug,
            meta={
                "topic_card": {"title": "T"},
                "montage_board": {"x": 1},
                "custom_prompts": {"n1": "p"},
                "prompt_slot_variants": {"n1": {"main": "v"}},
                "prompt_history": {"n1": []},
            },
        )
        session.add(sub)
        await session.flush()
        await batches_svc.clean_subprojects_meta(session)
        await session.refresh(sub)
        assert "montage_board" not in sub.meta
        assert sub.meta["custom_prompts"] == {"n1": "p"}
        assert sub.meta["prompt_slot_variants"] == {"n1": {"main": "v"}}
        assert sub.meta["prompt_history"] == {"n1": []}


@pytest.mark.asyncio
async def test_direct_status_write_blocked_in_strict_mode(monkeypatch) -> None:
    """D5: прямая запись nr.status ловится защитой."""
    from app import settings as app_settings
    from app.services.node_status_machine import _status_machine_write

    monkeypatch.setattr(app_settings.settings, "node_status_strict", True)
    project_id, nr_id, _wf_id = await _seed_project_with_run(
        node_status=NodeRunStatus.pending,
    )

    async with session_scope() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        token = _status_machine_write.set(False)
        try:
            with pytest.raises(RuntimeError, match="BYPASS"):
                nr.status = NodeRunStatus.done
        finally:
            _status_machine_write.reset(token)
