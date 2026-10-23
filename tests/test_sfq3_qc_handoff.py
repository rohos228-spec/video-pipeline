"""SFQ3: ③ не становится done без report.json; fail не является after."""

from __future__ import annotations

import json
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
from app.services.excel_gpt_node import (
    drop_completed_node_key,
    excel_gpt_already_completed_no_force,
    first_work_successor_along_edges,
    overflow_excel_gpt_predecessors,
    overflow_excel_gpt_successors,
    prepare_enrich_chain_for_auto_advance,
    sfq3_shots_report_exists,
)
from app.services.node_groups import all_groups, ensure_sfq3_after_edge
from app.services.run_sync import sync_run_for_project
from app.settings import settings

PLAN = "n_excel_gpt_sfq3_plan"
SCRIPT = "n_excel_gpt_sfq3_script"
SHOTS = "n_excel_gpt_sfq3_shots"


def _sfq3_nodes() -> list[dict]:
    specs = (
        (PLAN, "① Каркас батча (sfq3_plan)", 0),
        (SCRIPT, "② Кадры батча (sfq3_script)", 300),
        (SHOTS, "③ Проверка и отчёт (sfq3_shots)", 600),
    )
    return [
        {
            "id": nid,
            "type": "excel_gpt",
            "position": {"x": x, "y": 0},
            "data": {
                "label": label,
                "slotOverflow": True,
                "groupId": "script_frames_qc_v3",
            },
        }
        for nid, label, x in specs
    ]


def _edges_missing_after() -> list[dict]:
    """Как на tkach-testy: fail ③→② есть, after ②→③ нет."""
    return [
        {
            "id": "e_plan_script",
            "source": PLAN,
            "target": SCRIPT,
            "data": {"kind": "after"},
        },
        {
            "id": "e_shots_script",
            "source": SHOTS,
            "target": SCRIPT,
            "data": {"kind": "fail"},
        },
    ]


def _edges_full() -> list[dict]:
    return _edges_missing_after() + [
        {
            "id": "e_script_shots",
            "source": SCRIPT,
            "target": SHOTS,
            "data": {"kind": "after"},
        }
    ]


def _project(**meta_extra) -> SimpleNamespace:
    meta = {
        "canvas_graph": {"nodes": _sfq3_nodes(), "edges": _edges_full()},
        "excel_gpt_completed_keys": [PLAN, SCRIPT, SHOTS],
        "active_excel_gpt_node_key": SCRIPT,
    }
    meta.update(meta_extra)
    return SimpleNamespace(
        id=76,
        slug="tkach-testy",
        status=ProjectStatus.enrich_1_ready,
        meta=meta,
        data_dir=settings.data_dir / "videos" / "tkach-testy",
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


def _patch_default_workflow(monkeypatch, workflow_id: int) -> None:
    async def _wf_id(_session=None) -> int:
        return workflow_id

    monkeypatch.setattr("app.services.run_sync._get_default_workflow_id", _wf_id)


async def _seed_shots(
    scope,
    *,
    shots_status: NodeRunStatus,
    with_report: bool,
    report_schema: str = "sfq3",
) -> tuple[int, dict[str, int], int]:
    slug = f"sfq3-{uuid.uuid4().hex[:8]}"
    nodes = _sfq3_nodes()
    edges = _edges_missing_after()
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
            status=ProjectStatus.enrich_1_ready,
            meta={
                "canvas_graph": {"nodes": nodes, "edges": edges, "workflow_id": wf.id},
                "excel_gpt_completed_keys": [SHOTS],
                "active_excel_gpt_node_key": PLAN,
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
            status = shots_status if n["id"] == SHOTS else NodeRunStatus.pending
            nr = NodeRun(
                workflow_run_id=run.id,
                node_key=n["id"],
                node_type="excel_gpt",
                status=status,
            )
            session.add(nr)
            await session.flush()
            nr_ids[n["id"]] = nr.id
        project_id = project.id
        wf_id = wf.id
    if with_report:
        path = settings.data_dir / "videos" / slug / "reports" / "report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"schema": report_schema, "вердикт": "warn"}),
            encoding="utf-8",
        )
    return project_id, nr_ids, wf_id


def test_group_json_has_after_then_fail() -> None:
    group = all_groups()["script_frames_qc_v3"]
    assert ("script", "shots", "after") in group.internal_edges
    assert ("shots", "script", "fail") in group.internal_edges
    assert group.exit_key == "shots"


def test_fail_edge_is_not_forward_successor() -> None:
    p = _project()
    assert first_work_successor_along_edges(p, SCRIPT) == (SHOTS, "excel_gpt")
    assert first_work_successor_along_edges(p, SHOTS) is None
    assert SHOTS in overflow_excel_gpt_successors(p, SCRIPT)
    assert SCRIPT not in overflow_excel_gpt_successors(p, SHOTS)
    assert SHOTS not in overflow_excel_gpt_predecessors(p, SCRIPT)
    assert SCRIPT in overflow_excel_gpt_predecessors(p, SHOTS)


def test_ensure_restores_missing_after_edge_once() -> None:
    meta = {
        "canvas_graph": {
            "workflow_id": 1,
            "nodes": _sfq3_nodes(),
            "edges": _edges_missing_after(),
        }
    }
    assert ensure_sfq3_after_edge(meta) is True
    edges = meta["canvas_graph"]["edges"]
    forward = [
        e
        for e in edges
        if e["source"] == SCRIPT and e["target"] == SHOTS and e["data"]["kind"] == "after"
    ]
    assert len(forward) == 1
    assert any(
        e["source"] == SHOTS and e["target"] == SCRIPT and e["data"]["kind"] == "fail"
        for e in edges
    )
    assert ensure_sfq3_after_edge(meta) is False


def test_script_finish_clears_stale_shots_key() -> None:
    p = _project(
        canvas_graph={
            "nodes": _sfq3_nodes(),
            "edges": _edges_missing_after(),
        }
    )
    nxt = prepare_enrich_chain_for_auto_advance(
        p, ProjectStatus.enrich_1_ready, finished_key=SCRIPT
    )
    assert nxt is ProjectStatus.enriching_1
    assert p.meta["active_excel_gpt_node_key"] == SHOTS
    keys = p.meta["excel_gpt_completed_keys"]
    assert SCRIPT in keys
    assert SHOTS not in keys
    assert any(
        e.get("source") == SCRIPT and e.get("target") == SHOTS
        for e in p.meta["canvas_graph"]["edges"]
    )


def test_skip_requires_sfq3_report(tmp_path) -> None:
    p = _project()
    p.meta["active_excel_gpt_node_key"] = SHOTS
    p.data_dir = tmp_path
    assert sfq3_shots_report_exists(p, SHOTS) is False
    assert excel_gpt_already_completed_no_force(p) is False
    assert drop_completed_node_key(p, SHOTS) is True
    p.meta["excel_gpt_completed_keys"] = [SHOTS]
    p.meta["active_excel_gpt_node_key"] = SHOTS
    report = tmp_path / "reports" / "batches" / "B27.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"schema": "sfq3", "вердикт": "fail"}), encoding="utf-8")
    assert sfq3_shots_report_exists(p, SHOTS) is True
    assert excel_gpt_already_completed_no_force(p) is True
    report.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    assert excel_gpt_already_completed_no_force(p) is False


@pytest.mark.asyncio
async def test_heal_does_not_close_shots_without_report(mem_db, monkeypatch) -> None:
    project_id, nr_ids, wf_id = await _seed_shots(
        mem_db, shots_status=NodeRunStatus.pending, with_report=False
    )
    _patch_default_workflow(monkeypatch, wf_id)
    await sync_run_for_project(project_id)
    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids[SHOTS])
        assert nr is not None
        assert nr.status == NodeRunStatus.pending
        project = await session.get(Project, project_id)
        assert project is not None
        assert SHOTS not in (project.meta or {}).get("excel_gpt_completed_keys", [])


@pytest.mark.asyncio
async def test_heal_reopens_false_done_without_report(mem_db, monkeypatch) -> None:
    project_id, nr_ids, wf_id = await _seed_shots(
        mem_db, shots_status=NodeRunStatus.done, with_report=False
    )
    _patch_default_workflow(monkeypatch, wf_id)
    await sync_run_for_project(project_id)
    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids[SHOTS])
        assert nr is not None
        assert nr.status == NodeRunStatus.pending
        project = await session.get(Project, project_id)
        assert project is not None
        assert SHOTS not in (project.meta or {}).get("excel_gpt_completed_keys", [])


@pytest.mark.asyncio
async def test_heal_closes_shots_when_sfq3_report_exists(mem_db, monkeypatch) -> None:
    project_id, nr_ids, wf_id = await _seed_shots(
        mem_db, shots_status=NodeRunStatus.pending, with_report=True
    )
    _patch_default_workflow(monkeypatch, wf_id)
    await sync_run_for_project(project_id)
    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids[SHOTS])
        assert nr is not None
        assert nr.status == NodeRunStatus.done
        project = await session.get(Project, project_id)
        assert project is not None
        assert SHOTS in (project.meta or {}).get("excel_gpt_completed_keys", [])


@pytest.mark.asyncio
async def test_heal_rejects_report_with_wrong_schema(mem_db, monkeypatch) -> None:
    project_id, nr_ids, wf_id = await _seed_shots(
        mem_db,
        shots_status=NodeRunStatus.pending,
        with_report=True,
        report_schema="fw",
    )
    _patch_default_workflow(monkeypatch, wf_id)
    await sync_run_for_project(project_id)
    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids[SHOTS])
        assert nr is not None
        assert nr.status == NodeRunStatus.pending
