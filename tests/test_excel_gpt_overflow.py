"""excel_gpt с slotOverflow (слот 0): ▶ не должен давать 500."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

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
from app.orchestrator.graph.planner import WorkflowGraph
from app.orchestrator.node_registry import spec_for_node
from app.services.excel_gpt_node import (
    ready_status_for_slot,
    running_status_for_slot,
    slot_index_from_node,
)
from app.services.project_steps import start_step

# Группа «Сценарий → промпты кадров + QC» на канвасе: все 4 — overflow.
_OVERFLOW_GROUP = (
    ("n_excel_gpt_fw_script", "GPT: сценарист"),
    ("n_excel_gpt_fw_check_script", "Проверка: сценарий"),
    ("n_excel_gpt_fw_frames", "GPT: промты кадров · continuity"),
    ("n_excel_gpt_fw_qc", "GPT: QC промптов"),
)


def _overflow_node(nid: str, label: str, x: float) -> dict:
    return {
        "id": nid,
        "type": "excel_gpt",
        "position": {"x": x, "y": 0},
        "data": {
            "label": label,
            "slotOverflow": True,
            "groupId": "scenariy_prompty_kadrov_qc",
            "groupTitle": "Сценарий → промпты кадров + QC",
        },
    }


def _overflow_nodes() -> list[dict]:
    return [
        _overflow_node(nid, label, i * 290)
        for i, (nid, label) in enumerate(_OVERFLOW_GROUP)
    ]


def test_overflow_slot_is_zero() -> None:
    n = _overflow_node("n_x", "GPT: сценарист", 0)
    assert slot_index_from_node(n) == 0


def test_running_status_for_overflow_slot_does_not_raise() -> None:
    """Раньше KeyError: 0 → HTTP 500 на ▶ overflow-ноды."""
    assert running_status_for_slot(0) is ProjectStatus.enriching_1
    assert ready_status_for_slot(0) is ProjectStatus.enrich_1_ready
    assert running_status_for_slot(99) is ProjectStatus.enriching_1


def test_spec_for_overflow_excel_gpt() -> None:
    spec = spec_for_node(_overflow_node("n_x", "GPT: сценарист", 0))
    assert spec is not None
    assert spec.step_code == "excel_gpt"
    assert spec.running_status is ProjectStatus.enriching_1
    assert spec.ready_status is ProjectStatus.enrich_1_ready


def test_derived_states_overflow_group_does_not_crash() -> None:
    nodes = _overflow_nodes()
    edges = [
        {
            "id": f"e_{a}_{b}",
            "source": a,
            "target": b,
            "data": {"kind": "after"},
        }
        for (a, _), (b, _) in zip(_OVERFLOW_GROUP, _OVERFLOW_GROUP[1:])
    ]
    graph = WorkflowGraph(nodes, edges)
    project = SimpleNamespace(
        status=ProjectStatus.plan_ready,
        meta={"canvas_graph": {"nodes": nodes, "edges": edges}},
        topic="t",
    )
    states = graph.derived_node_states(project)
    assert set(states) >= {n["id"] for n in nodes}


def test_prepare_overflow_chain_advances_to_next() -> None:
    from app.services.excel_gpt_node import prepare_enrich_chain_for_auto_advance

    nodes = _overflow_nodes()
    edges = [
        {"id": "e1", "source": "n_excel_gpt_fw_script", "target": "n_excel_gpt_fw_check_script"},
        {"id": "e2", "source": "n_excel_gpt_fw_check_script", "target": "n_excel_gpt_fw_frames"},
        {"id": "e3", "source": "n_excel_gpt_fw_frames", "target": "n_excel_gpt_fw_qc"},
    ]
    p = SimpleNamespace(
        id=60,
        status=ProjectStatus.enrich_1_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "active_excel_gpt_node_key": "n_excel_gpt_fw_script",
            "excel_gpt_completed_keys": ["n_excel_gpt_fw_script"],
        },
    )
    nxt = prepare_enrich_chain_for_auto_advance(
        p,
        ProjectStatus.enrich_1_ready,
        finished_key="n_excel_gpt_fw_script",
    )
    assert nxt is ProjectStatus.enriching_1
    assert p.meta["active_excel_gpt_node_key"] == "n_excel_gpt_fw_check_script"


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


async def _seed_overflow_group(scope):
    slug = f"ovf-{uuid.uuid4().hex[:8]}"
    nodes = _overflow_nodes()
    edges = [
        {"id": "e1", "source": "n_excel_gpt_fw_script", "target": "n_excel_gpt_fw_check_script"},
        {"id": "e2", "source": "n_excel_gpt_fw_check_script", "target": "n_excel_gpt_fw_frames"},
        {"id": "e3", "source": "n_excel_gpt_fw_frames", "target": "n_excel_gpt_fw_qc"},
    ]
    async with scope() as session:
        wf = Workflow(
            name=f"wf-{uuid.uuid4().hex[:8]}",
            is_default=True,
            nodes=nodes,
            edges=edges,
        )
        session.add(wf)
        await session.flush()
        project = Project(
            slug=slug,
            topic="t",
            status=ProjectStatus.plan_ready,
            meta={
                "canvas_graph": {
                    "workflow_id": wf.id,
                    "nodes": nodes,
                    "edges": edges,
                }
            },
        )
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=nodes,
            edges_snapshot=edges,
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
@pytest.mark.parametrize("node_key,label", _OVERFLOW_GROUP)
async def test_start_step_overflow_group_nodes(
    mem_db, tmp_path, monkeypatch, node_key: str, label: str
) -> None:
    """Каждая нода группы «Сценарий → промпты кадров + QC» стартует без KeyError."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    project_id, wf_id, nr_ids = await _seed_overflow_group(mem_db)
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
            node_key=node_key,
            skip_queue_guard=True,
            require_node_fsm=True,
            explicit_ui_start=True,
        )
        assert status is ProjectStatus.enriching_1, label
        assert (project.meta or {}).get("active_excel_gpt_node_key") == node_key

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids[node_key])
        assert nr is not None and nr.status == NodeRunStatus.running, label
