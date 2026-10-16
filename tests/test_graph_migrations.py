from __future__ import annotations

import copy

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun,
)
from app.orchestrator.graph.planner import WorkflowGraph
from app.orchestrator.graph.validate import validate_workflow_graph
from app.services import graph_migrations as gm
from app.services.excel_gpt_node import slot_index_from_node


def _legacy_graph():
    nodes = [
        {"id": "n_split", "type": "split", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_e1", "type": "enrich_1", "position": {"x": 100, "y": 0},
         "data": {"label": "Проверка закадра", "inputSource": "voiceover", "workMode": "review", "custom": {"a": [1, 2]}}},
        {"id": "n_e3", "type": "enrich_3", "position": {"x": 200, "y": 0}, "data": {}},
        {"id": "n_e2", "type": "enrich_2", "position": {"x": 300, "y": 0},
         "data": {"slotIndex": 4, "label": "Доп работа с Excel 2"}},
        {"id": "n_eo", "type": "enrich_5", "position": {"x": 400, "y": 0}, "data": {"slotOverflow": True}},
        {"id": "n_hero", "type": "hero", "position": {"x": 500, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_split", "target": "n_e1"},
        {"id": "e2", "source": "n_e1", "target": "n_e3"},
        {"id": "e3", "source": "n_e3", "target": "n_e2"},
        {"id": "e4", "source": "n_e2", "target": "n_eo"},
        {"id": "e5", "source": "n_eo", "target": "n_hero"},
    ]
    return nodes, edges


def test_pure_migration_changes_type_keeps_slot_and_params():
    nodes, edges = _legacy_graph()
    before = copy.deepcopy(nodes)
    res = gm.migrate_enrich_graph(nodes)
    assert nodes == before, "the input is never mutated"
    by_id = {n["id"]: n for n in res.nodes}
    assert [n["id"] for n in res.nodes] == [n["id"] for n in nodes]
    assert all(n["type"] == "excel_gpt" for n in res.nodes if n["id"].startswith("n_e"))
    assert by_id["n_split"] == nodes[0] and by_id["n_hero"] == nodes[-1]
    for old in nodes:
        assert slot_index_from_node(old) == slot_index_from_node(by_id[old["id"]]), old["id"]
    assert by_id["n_e1"]["data"]["slotIndex"] == 1 and by_id["n_e3"]["data"]["slotIndex"] == 3
    assert by_id["n_e2"]["data"]["slotIndex"] == 4, "an explicit slot is not overridden by the type"
    assert "slotIndex" not in by_id["n_eo"]["data"] and by_id["n_eo"]["data"]["slotOverflow"] is True
    assert by_id["n_e1"]["data"]["inputSource"] == "voiceover" and by_id["n_e1"]["data"]["custom"] == {"a": [1, 2]}
    assert by_id["n_e1"]["data"]["label"] == "Проверка закадра"
    assert by_id["n_e3"]["data"]["label"] == "ИИ-редактор сцен"
    assert by_id["n_e1"]["position"] == nodes[1]["position"]
    assert [c.node_id for c in res.changes] == ["n_e1", "n_e3", "n_e2", "n_eo"]
    assert res.rollback == [n for n in before if n["type"].startswith("enrich_")]


def test_migrated_graph_validates_and_resolves_nodes():
    nodes, edges = _legacy_graph()
    assert validate_workflow_graph(nodes, edges)["valid"]
    old = WorkflowGraph(nodes, edges)
    assert old.node_type("n_e1") == "enrich_1" and old.excel_gpt_keys_for_slot(1) == []
    res = gm.migrate_enrich_graph(nodes)
    check = validate_workflow_graph(res.nodes, edges)
    assert check["valid"], check["errors"]
    graph = WorkflowGraph(res.nodes, edges)
    assert {graph.node_type(k) for k in ("n_e1", "n_e2", "n_e3", "n_eo")} == {"excel_gpt"}
    assert graph.excel_gpt_keys_for_slot(1) == ["n_e1"]
    assert graph.excel_gpt_keys_for_slot(3) == ["n_e3"]
    assert graph.excel_gpt_keys_for_slot(4) == ["n_e2"]
    project = Project(topic="t", slug="gm", status=ProjectStatus.enrich_1_ready, meta={"excel_gpt_completed_keys": ["n_e1"]})
    found = graph.next_work_node_after_ready(project, ProjectStatus.enrich_1_ready)
    assert found is not None and graph.node_type(found[0]) == "excel_gpt"
    assert found[0] == "n_e3"


def test_migration_is_idempotent_and_leaves_clean_graphs_alone():
    nodes, _ = _legacy_graph()
    first = gm.migrate_enrich_graph(nodes)
    second = gm.migrate_enrich_graph(first.nodes)
    assert not second.changed and not second.skipped and second.rollback == []
    assert second.nodes == first.nodes
    clean = [{"id": "a", "type": "excel_gpt", "data": {"slotIndex": 2}}, {"id": "b", "type": "hero", "data": {}}]
    assert gm.migrate_enrich_graph(clean).nodes == clean and not gm.migrate_enrich_graph(clean).changed
    assert gm.migrate_enrich_graph(None).nodes == [] and gm.migrate_enrich_graph([]).nodes == []


def test_scene_marker_and_unrelated_types_are_not_touched():
    nodes = [{"id": "sd", "type": "excel_gpt", "data": {"sd_agent": "world"}},
             {"id": "weird", "type": "enrich_9", "data": {}}, {"id": "plain", "type": "enrich", "data": {}}]
    res = gm.migrate_enrich_graph(nodes)
    assert res.nodes == nodes and not res.changed


def test_rollback_restores_the_original_nodes_and_skips_edited_ones():
    nodes, _ = _legacy_graph()
    res = gm.migrate_enrich_graph(nodes)
    edited = [dict(n, type="hero") if n["id"] == "n_e3" else n for n in res.nodes]
    restored, ids = gm.rollback_enrich_graph(edited, res.rollback)
    assert ids == ["n_e1", "n_e2", "n_eo"]
    by_id = {n["id"]: n for n in restored}
    assert by_id["n_e1"] == nodes[1] and by_id["n_e2"] == nodes[3] and by_id["n_eo"] == nodes[4]
    assert by_id["n_e3"]["type"] == "hero"
    full, all_ids = gm.rollback_enrich_graph(res.nodes, res.rollback)
    assert full == nodes and len(all_ids) == 4


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session):
    nodes, edges = _legacy_graph()
    wf = Workflow(name="old", nodes=copy.deepcopy(nodes), edges=copy.deepcopy(edges), version=3, meta={})
    clean = Workflow(name="clean", nodes=[{"id": "x", "type": "excel_gpt", "data": {"slotIndex": 1}}], edges=[], version=1, meta={})
    session.add_all([wf, clean])
    await session.flush()
    project = Project(topic="t", slug="gm-1", status=ProjectStatus.enrich_1_ready,
                      meta={"canvas_graph": {"workflow_id": wf.id, "nodes": copy.deepcopy(nodes), "edges": edges, "saved_at": "x"},
                            "keep": 1})
    run = WorkflowRun(workflow_id=wf.id, project_id=1, nodes_snapshot=copy.deepcopy(nodes), edges_snapshot=edges, meta={})
    session.add_all([project, run])
    await session.flush()
    session.add_all([NodeRun(workflow_run_id=run.id, node_key="n_e1", node_type="enrich_1"),
                     NodeRun(workflow_run_id=run.id, node_key="n_hero", node_type="hero")])
    await session.commit()
    return wf, clean, project, run


@pytest.mark.asyncio
async def test_dry_run_reports_and_writes_nothing(session):
    wf, clean, project, run = await _seed(session)
    report = await gm.migrate_stores(session, dry_run=True)
    await session.commit()
    assert report["dry_run"] is True and report["stores_changed"] == 3 and report["nodes_changed"] == 12
    assert {i["store"] for i in report["items"]} == {"workflow", "workflow_run", "project_canvas"}
    assert all(n["type"].startswith("enrich_") for n in (await session.get(Workflow, wf.id)).nodes if n["id"].startswith("n_e"))
    assert (await session.get(Workflow, wf.id)).version == 3
    assert gm.GRAPH_MIGRATIONS_META_KEY not in (await session.get(Workflow, wf.id)).meta
    assert (await session.get(Project, project.id)).meta["canvas_graph"]["nodes"][1]["type"] == "enrich_1"
    assert (await session.get(WorkflowRun, run.id)).meta == {}


@pytest.mark.asyncio
async def test_apply_is_idempotent_and_rollback_restores_everything(session):
    wf, clean, project, run = await _seed(session)
    ids = (wf.id, clean.id, project.id, run.id)
    originals = copy.deepcopy(_legacy_graph()[0])
    report = await gm.migrate_stores(session, dry_run=False)
    await session.commit()
    assert report["nodes_changed"] == 12
    got = await session.get(Workflow, ids[0])
    assert got.version == 4 and not any(str(n["type"]).startswith("enrich_") for n in got.nodes)
    assert got.meta[gm.GRAPH_MIGRATIONS_META_KEY][0]["id"] == gm.ENRICH_TO_EXCEL_GPT_ID
    assert got.meta[gm.GRAPH_MIGRATIONS_META_KEY][0]["before"][0]["type"] == "enrich_1"
    assert (await session.get(Workflow, ids[1])).version == 1 and gm.GRAPH_MIGRATIONS_META_KEY not in (await session.get(Workflow, ids[1])).meta
    proj = await session.get(Project, ids[2])
    assert proj.meta["keep"] == 1 and proj.meta["canvas_graph"]["saved_at"] == "x"
    assert not any(str(n["type"]).startswith("enrich_") for n in proj.meta["canvas_graph"]["nodes"])
    assert validate_workflow_graph(got.nodes, got.edges)["valid"]
    node_runs = {nr.node_key: nr.node_type for nr in (await session.execute(
        __import__("sqlalchemy").select(NodeRun))).scalars().all()}
    assert node_runs == {"n_e1": "excel_gpt", "n_hero": "hero"}

    again = await gm.migrate_stores(session, dry_run=False)
    await session.commit()
    assert again["stores_changed"] == 0 and again["nodes_changed"] == 0 and again["items"] == []
    assert len((await session.get(Workflow, ids[0])).meta[gm.GRAPH_MIGRATIONS_META_KEY]) == 1
    assert (await session.get(Workflow, ids[0])).version == 4

    back = await gm.rollback_stores(session)
    await session.commit()
    assert {r["store"] for r in back["restored"]} == {"workflow", "workflow_run", "project_canvas"}
    session.expire_all()
    assert (await session.get(Workflow, ids[0])).nodes == originals
    assert gm.GRAPH_MIGRATIONS_META_KEY not in (await session.get(Workflow, ids[0])).meta
    assert (await session.get(Project, ids[2])).meta["canvas_graph"]["nodes"] == originals
    assert (await session.get(WorkflowRun, ids[3])).nodes_snapshot == originals
    node_runs = {nr.node_key: nr.node_type for nr in (await session.execute(
        __import__("sqlalchemy").select(NodeRun))).scalars().all()}
    assert node_runs == {"n_e1": "enrich_1", "n_hero": "hero"}
    assert (await gm.rollback_stores(session))["restored"] == [], "a second rollback is a no-op"
