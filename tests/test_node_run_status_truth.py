"""P0: NodeRun status machine as single source of truth."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
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
from app.services.project_meta import merge_project_meta
from app.services.project_steps import start_step
from app.services.run_sync import (
    _reconcile_stale_node_runs,
    prepare_node_for_step_start,
    sync_run_for_project,
)


@pytest.fixture
async def mem_db(monkeypatch):
    """In-memory DB + патч session_scope для run_sync / тестов."""
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


async def _seed_project_with_run(
    scope,
    *,
    project_status: ProjectStatus = ProjectStatus.plan_ready,
    node_status: NodeRunStatus = NodeRunStatus.pending,
    node_type: str = "plan",
    node_key: str = "n_plan",
) -> tuple[int, int, int]:
    """Returns (project_id, node_run_id, workflow_id)."""
    slug = f"nr-truth-{uuid.uuid4().hex[:8]}"
    async with scope() as session:
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
    async def _wf_id(_session=None) -> int:
        return workflow_id

    monkeypatch.setattr(
        "app.services.run_sync._get_default_workflow_id",
        _wf_id,
    )


@pytest.mark.asyncio
async def test_run_sync_never_promotes_to_done(mem_db) -> None:
    """D1: sync_run не переводит pending → done из project.status."""
    project_id, nr_id, _wf_id = await _seed_project_with_run(
        mem_db,
        project_status=ProjectStatus.plan_ready,
        node_status=NodeRunStatus.pending,
    )

    await sync_run_for_project(project_id)

    async with mem_db() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.pending


@pytest.mark.asyncio
async def test_stale_running_reconciled_to_failed(mem_db, monkeypatch) -> None:
    """D2: зависшая running без живой задачи → failed."""
    from app.services import step_cancel as sc

    project_id, nr_id, _wf_id = await _seed_project_with_run(
        mem_db,
        project_status=ProjectStatus.planning,
        node_status=NodeRunStatus.running,
    )
    async with mem_db() as session:
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

    async with mem_db() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.failed
        assert "рабочий процесс не активен" in (row.error or "")


@pytest.mark.asyncio
async def test_stale_running_healed_when_project_already_ready(mem_db, monkeypatch) -> None:
    """Гонка: Project уже script_ready, NodeRun ещё running → heal done, не failed."""
    from app.services import step_cancel as sc

    project_id, nr_id, _wf_id = await _seed_project_with_run(
        mem_db,
        project_status=ProjectStatus.script_ready,
        node_status=NodeRunStatus.running,
        node_type="script",
        node_key="n_script",
    )
    async with mem_db() as session:
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

    async with mem_db() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.done
        assert not (row.error or "")


@pytest.mark.asyncio
async def test_prepare_auto_unstick_stale_running(mem_db, monkeypatch) -> None:
    """D2: auto_unstick — запуск зависшей running из UI."""
    from app.services import step_cancel as sc

    project_id, nr_id, wf_id = await _seed_project_with_run(
        mem_db,
        project_status=ProjectStatus.planning,
        node_status=NodeRunStatus.running,
    )
    _patch_default_workflow(monkeypatch, wf_id)
    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.general_plan = "x" * 200

    monkeypatch.setattr(sc, "is_generation_active", lambda _pid: False)

    async with mem_db() as session:
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

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        assert nr.status == NodeRunStatus.running


@pytest.mark.asyncio
async def test_ui_restart_done_node(mem_db, monkeypatch) -> None:
    """D3: явный UI-запуск done-ноды → рестарт без ошибки."""
    project_id, nr_id, wf_id = await _seed_project_with_run(
        mem_db,
        project_status=ProjectStatus.plan_ready,
        node_status=NodeRunStatus.done,
    )
    _patch_default_workflow(monkeypatch, wf_id)
    async with mem_db() as session:
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

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        assert nr.status == NodeRunStatus.running


@pytest.mark.asyncio
async def test_auto_start_rejects_done_without_ui_flag(mem_db, monkeypatch) -> None:
    """D3: без explicit_ui_start done-нода не перезапускается."""
    project_id, nr_id, wf_id = await _seed_project_with_run(
        mem_db,
        project_status=ProjectStatus.plan_ready,
        node_status=NodeRunStatus.done,
    )
    _patch_default_workflow(monkeypatch, wf_id)

    async with mem_db() as session:
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

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        assert nr.status == NodeRunStatus.done


@pytest.mark.asyncio
async def test_start_step_passes_explicit_ui_restart(mem_db, tmp_path, monkeypatch) -> None:
    """D3: start_step с explicit_ui_start перезапускает done."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")

    project_id, nr_id, wf_id = await _seed_project_with_run(
        mem_db,
        project_status=ProjectStatus.plan_ready,
        node_status=NodeRunStatus.done,
        node_type="script",
        node_key="n_script",
    )
    _patch_default_workflow(monkeypatch, wf_id)
    async with mem_db() as session:
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

    async with mem_db() as session:
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
async def test_direct_status_write_blocked_in_strict_mode(mem_db, monkeypatch) -> None:
    """D5: прямая запись nr.status ловится защитой."""
    from app import settings as app_settings
    from app.services.node_status_machine import _status_machine_write

    monkeypatch.setattr(app_settings.settings, "node_status_strict", True)
    project_id, nr_id, _wf_id = await _seed_project_with_run(
        mem_db,
        node_status=NodeRunStatus.pending,
    )

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        token = _status_machine_write.set(False)
        try:
            with pytest.raises(RuntimeError, match="BYPASS"):
                nr.status = NodeRunStatus.done
        finally:
            _status_machine_write.reset(token)


@pytest.mark.asyncio
async def test_assembled_heals_failed_run_status(mem_db) -> None:
    """D14: assembled + stale failed NodeRun → WorkflowRun done, NodeRun healed."""
    project_id, nr_id, _wf_id = await _seed_project_with_run(
        mem_db,
        project_status=ProjectStatus.assembled,
        node_status=NodeRunStatus.failed,
        node_type="music",
        node_key="n_music",
    )
    async with mem_db() as session:
        run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.project_id == project_id)
            )
        ).scalar_one()
        run.status = WorkflowRunStatus.failed
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        nr.error = "прервано: рабочий процесс не активен"
        # unused side node stays pending — must not keep run failed/waiting
        session.add(
            NodeRun(
                workflow_run_id=run.id,
                node_key="n_excel_gpt_side",
                node_type="excel_gpt",
                status=NodeRunStatus.pending,
            )
        )

    await sync_run_for_project(project_id)

    async with mem_db() as session:
        run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.project_id == project_id)
            )
        ).scalar_one()
        assert run.status == WorkflowRunStatus.done
        nr = await session.get(NodeRun, nr_id)
        assert nr is not None
        assert nr.status == NodeRunStatus.done
        assert not (nr.error or "")


@pytest.mark.asyncio
async def test_scene_assembling_heals_false_failed_excel_gpt_sd_agents(mem_db) -> None:
    """Веер на канвасе — excel_gpt+sd_marker; NodeRun.type=excel_gpt.

    После agents_done reconcile красил ноды в «прервано…» — UI «везде ошибки»,
    хотя meta.agents=done и идёт assemble. sync должен: type→sd_*, failed→done,
    sd_asm → running.
    """
    slug = f"sd-fan-{uuid.uuid4().hex[:8]}"
    agents = ("characters", "world", "camera", "action")
    async with mem_db() as session:
        wf = Workflow(name=f"wf-{uuid.uuid4().hex[:8]}", is_default=False, nodes=[], edges=[])
        session.add(wf)
        await session.flush()
        project = Project(
            slug=slug, topic="t", status=ProjectStatus.scene_assembling
        )
        session.add(project)
        await session.flush()
        nodes = []
        for a in agents:
            nodes.append(
                {
                    "id": f"n_excel_gpt_sd_{a}",
                    "type": "excel_gpt",
                    "data": {"sd_agent": a},
                }
            )
        nodes.append(
            {
                "id": "n_excel_gpt_sd_asm",
                "type": "excel_gpt",
                "data": {"sd_agent": "assemble"},
            }
        )
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.failed,
            nodes_snapshot=nodes,
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        agent_ids: list[int] = []
        for a in agents:
            nr = NodeRun(
                workflow_run_id=run.id,
                node_key=f"n_excel_gpt_sd_{a}",
                node_type="excel_gpt",
                status=NodeRunStatus.failed,
                error="прервано: рабочий процесс не активен",
            )
            session.add(nr)
            await session.flush()
            agent_ids.append(nr.id)
        asm = NodeRun(
            workflow_run_id=run.id,
            node_key="n_excel_gpt_sd_asm",
            node_type="excel_gpt",
            status=NodeRunStatus.pending,
        )
        session.add(asm)
        await session.flush()
        asm_id = asm.id
        project_id = project.id

    await sync_run_for_project(project_id)

    async with mem_db() as session:
        for nid in agent_ids:
            nr = await session.get(NodeRun, nid)
            assert nr is not None
            assert nr.node_type == "sd_agent"
            assert nr.status == NodeRunStatus.done
            assert not (nr.error or "")
        asm_row = await session.get(NodeRun, asm_id)
        assert asm_row is not None
        assert asm_row.node_type == "sd_assemble"
        assert asm_row.status == NodeRunStatus.running


async def _seed_excel_gpt_overflow(
    scope,
    *,
    project_status: ProjectStatus,
    node_status: NodeRunStatus,
    node_key: str = "n_excel_gpt_fw_script",
    completed_keys: list[str] | None = None,
    started_ago_sec: float = 120,
) -> tuple[int, int]:
    slug = f"egpt-nr-{uuid.uuid4().hex[:8]}"
    async with scope() as session:
        wf = Workflow(
            name=f"wf-{uuid.uuid4().hex[:8]}",
            is_default=False,
            nodes=[],
            edges=[],
        )
        session.add(wf)
        await session.flush()
        meta: dict = {}
        if completed_keys:
            meta["excel_gpt_completed_keys"] = list(completed_keys)
        project = Project(
            slug=slug,
            topic="t",
            status=project_status,
            meta=meta,
        )
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.running,
            nodes_snapshot=[{"id": node_key, "type": "excel_gpt"}],
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        nr = NodeRun(
            workflow_run_id=run.id,
            node_key=node_key,
            node_type="excel_gpt",
            status=node_status,
            started_at=datetime.utcnow() - timedelta(seconds=started_ago_sec),
            error=(
                "прервано: рабочий процесс не активен"
                if node_status == NodeRunStatus.failed
                else None
            ),
        )
        session.add(nr)
        await session.flush()
        return project.id, nr.id


@pytest.mark.asyncio
async def test_excel_gpt_overflow_reconcile_heals_from_completed_keys(
    mem_db, monkeypatch
) -> None:
    """Overflow excel_gpt в completed_keys, NodeRun ещё running → done, не failed."""
    from app.services import step_cancel as sc

    _pid, nr_id = await _seed_excel_gpt_overflow(
        mem_db,
        project_status=ProjectStatus.enrich_1_ready,
        node_status=NodeRunStatus.running,
        node_key="n_excel_gpt_fw_script",
        completed_keys=["n_excel_gpt_fw_script"],
    )
    monkeypatch.setattr(sc, "is_generation_active", lambda _pid: False)

    fixed = await _reconcile_stale_node_runs(
        initiator="background_reconcile",
        require_no_live_task=True,
        grace_sec=0,
    )
    assert fixed >= 1

    async with mem_db() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.done
        assert "рабочий процесс не активен" not in (row.error or "")


@pytest.mark.asyncio
async def test_excel_gpt_keep_running_while_enriching(
    mem_db, monkeypatch
) -> None:
    """GPT-батч дольше grace: не красить excel_gpt, пока project enriching_N."""
    from app.services import step_cancel as sc

    _pid, nr_id = await _seed_excel_gpt_overflow(
        mem_db,
        project_status=ProjectStatus.enriching_1,
        node_status=NodeRunStatus.running,
        node_key="n_excel_gpt_fw_frames",
        completed_keys=None,
    )
    monkeypatch.setattr(sc, "is_generation_active", lambda _pid: False)

    fixed = await _reconcile_stale_node_runs(
        initiator="background_reconcile",
        require_no_live_task=True,
        grace_sec=0,
    )
    assert fixed == 0

    async with mem_db() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.running


@pytest.mark.asyncio
async def test_excel_gpt_false_failed_heals_from_completed_keys(mem_db) -> None:
    """Ложный background_reconcile: failed + ключ в completed_keys → done."""
    _pid, nr_id = await _seed_excel_gpt_overflow(
        mem_db,
        project_status=ProjectStatus.enrich_1_ready,
        node_status=NodeRunStatus.failed,
        node_key="n_excel_gpt_fw_qc",
        completed_keys=["n_excel_gpt_fw_qc"],
    )

    fixed = await _reconcile_stale_node_runs(initiator="background_reconcile")
    assert fixed >= 1

    async with mem_db() as session:
        row = await session.get(NodeRun, nr_id)
        assert row is not None
        assert row.status == NodeRunStatus.done
