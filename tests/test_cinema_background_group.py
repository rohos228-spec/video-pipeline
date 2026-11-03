"""Independent cinema preset, prompt alias, and legacy migration isolation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow, WorkflowRun, WorkflowRunStatus
from app.services import node_groups as ng
from app.services import prompt_library as pl

GID = "cinema_with_background"


def _meta(group_id=GID, *, legacy_ids=False):
    group = ng.NODE_GROUPS[GID]
    ids = {
        s.local_key: s.preferred_id.replace("n_cinema_", "n_", 1) if legacy_ids else s.preferred_id
        for s in group.nodes
    }
    return {
        "canvas_graph": {
            "workflow_id": 1,
            "nodes": [
                {"id": ids[s.local_key], "type": s.node_type,
                 "position": {"x": s.dx, "y": s.dy},
                 "data": {"groupId": group_id, "label": s.label}}
                for s in group.nodes
            ],
            "edges": [
                {"id": f"e_{a}_{b}", "source": ids[a], "target": ids[b], "data": {"kind": kind}}
                for a, b, kind in group.internal_edges
            ],
        },
        "prompt_slot_variants": {
            ids[s.local_key]: {"main": s.prompt_variant} for s in group.nodes if s.prompt_variant
        },
    }


def test_catalog_and_legacy_unchanged():
    old = ng.NODE_GROUPS[ng.SCRIPT_FRAMES_QC_GROUP_ID]
    new = ng.NODE_GROUPS[GID]
    assert new.title == "кино С фоном"
    assert [s.local_key for s in old.nodes] == [
        "boundaries", "script", "action", "shots", "qc", "style", "report"
    ]
    assert next(s for s in old.nodes if s.local_key == "action").prompt_variant == "action"
    assert [s.local_key for s in new.nodes] == [
        "boundaries", "script", "action", "shots", "qc", "style", "backgrounds", "report"
    ]
    for s in new.nodes:
        assert s.preferred_id == f"n_cinema_excel_gpt_fw_{s.local_key}"
        assert s.node_type == "excel_gpt"
    bg = next(s for s in new.nodes if s.local_key == "backgrounds")
    assert bg.operator_config == {"transport": "code", "outputMode": "artifact"}
    assert bg.slot_overflow
    assert ("style", "backgrounds", "after") in new.internal_edges
    assert ("backgrounds", "report", "after") in new.internal_edges
    assert ("style", "report", "after") not in new.internal_edges
    assert new.entry_keys == old.entry_keys and new.exit_key == "report"


@pytest.mark.parametrize("instance", [GID, GID + "#2"])
def test_detection_alias_and_shared_prompts(instance):
    meta = _meta(instance)
    project = SimpleNamespace(meta=meta)
    assert ng.canvas_has_cinema_backgrounds(meta)
    assert ng.canvas_has_cinema_backgrounds(project)
    assert ng.canvas_has_script_frames_qc(project)
    assert not ng.canvas_has_cinema_backgrounds(_meta("script_frames_qc", legacy_ids=True))
    assert pl.script_frames_qc_group_id(instance) == "script_frames_qc"
    assert pl.node_group_prompts_dir(instance) == pl.node_group_prompts_dir("script_frames_qc")
    names = pl.list_group_owned_prompts(instance)
    assert "cinema_place_inventory_ru" in names
    for name in ("scene_skeleton_agent", "scenes_to_frames_ru", "shots_qc_ru", "cinema_place_inventory_ru"):
        assert pl.resolve_excel_gpt_prompt_path(name, group_id=instance) == pl.resolve_script_frames_qc_prompt_path(name)
    assert pl.uses_script_frames_qc_prompts(project=project, node_key="n_cinema_excel_gpt_fw_action")
    assert ng.is_script_frames_qc_gpt_node(meta, node_key="n_cinema_excel_gpt_fw_action")
    for key in ("style", "report", "backgrounds"):
        assert not ng.is_script_frames_qc_gpt_node(meta, node_key=f"n_cinema_excel_gpt_fw_{key}")


def test_inventory_prompt_recognized_by_existing_enrich_detector():
    from app.orchestrator.steps.enrich_xlsx import _is_main_action_node, _is_place_inventory_prompt

    path = pl.resolve_script_frames_qc_prompt_path("cinema_place_inventory_ru")
    master = path.read_text(encoding="utf-8")
    assert _is_place_inventory_prompt("cinema_place_inventory_ru", master)
    assert not _is_main_action_node("cinema_place_inventory_ru", master, "n_cinema_excel_gpt_fw_action")
    assert "main_action + место + сцена + персонажи" in master
    assert '"ops"' in master and '"площадка"' in master and '"меняет"' in master
    assert "Ткач" not in master


@pytest.mark.parametrize("legacy_ids", [False, True])
def test_all_legacy_migrations_leave_cinema_untouched(legacy_ids):
    meta = _meta(GID + "#2", legacy_ids=legacy_ids)
    original = deepcopy(meta)
    migrations = [
        ng.upgrade_script_frames_qc_graph,
        ng.drop_script_frames_qc_action_graph,
        ng.drop_script_frames_qc_check_graph,
        ng.restore_script_frames_qc_check_and_action_graph,
        ng.relabel_script_frames_qc_chain,
        ng.rewire_script_frames_qc_shots_to_qc,
        ng.restore_script_frames_qc_four_node_graph,
        ng.upgrade_script_frames_qc_report_graph,
        ng.insert_script_frames_qc_style_graph,
        ng.insert_script_frames_qc_boundaries_graph,
    ]
    for migrate in migrations:
        assert not migrate(meta), migrate.__name__
        assert meta == original, migrate.__name__
    assert not ng.script_frames_qc_needs_upgrade(meta)


def test_mixed_canvas_legacy_upgrade_preserves_cinema():
    meta = _meta()
    cinema_nodes = deepcopy(meta["canvas_graph"]["nodes"])
    cinema_edges = deepcopy(meta["canvas_graph"]["edges"])
    meta["canvas_graph"]["nodes"].extend([
        {"id": "n_excel_gpt_fw_script", "type": "excel_gpt", "position": {"x": 0, "y": 0}, "data": {"groupId": "script_frames_qc"}},
        {"id": "n_excel_gpt_fw_shots", "type": "excel_gpt", "position": {"x": 600, "y": 0}, "data": {"groupId": "script_frames_qc"}},
    ])
    assert ng.restore_script_frames_qc_check_and_action_graph(meta)
    assert all(n in meta["canvas_graph"]["nodes"] for n in cinema_nodes)
    assert all(e in meta["canvas_graph"]["edges"] for e in cinema_edges)
    assert meta["prompt_slot_variants"]["n_cinema_excel_gpt_fw_action"] == {"main": "cinema_place_inventory_ru"}


async def test_insert_group_and_collisions(monkeypatch, tmp_path):
    monkeypatch.setattr(ng, "custom_groups_dir", lambda: tmp_path / "node_groups")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            nodes = [{"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {}}]
            wf = Workflow(name="cinema-test", is_default=True, nodes=nodes, edges=[])
            session.add(wf)
            await session.flush()
            project = Project(slug="cinema-test", topic="test", status=ProjectStatus.new,
                              meta={"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": []}})
            session.add(project)
            await session.flush()
            session.add(WorkflowRun(project_id=project.id, workflow_id=wf.id,
                                    status=WorkflowRunStatus.new, nodes_snapshot=nodes, edges_snapshot=[]))
            await session.commit()
            for gid in ("script_frames_qc", GID):
                await ng.insert_node_group(session, project, gid)
            with pytest.raises(ValueError, match="повторная вставка"):
                await ng.insert_node_group(session, project, GID)
            meta = project.meta
            assert ng.canvas_has_cinema_backgrounds(project)
            ids = [n["id"] for n in meta["canvas_graph"]["nodes"]]
            assert len(ids) == len(set(ids))
            assert "n_excel_gpt_fw_action" in ids
            for nid in ids:
                if "_fw_backgrounds" in nid:
                    assert meta["excel_gpt_nodes"][nid]["transport"] == "code"
                    assert not ng.is_script_frames_qc_gpt_node(meta, node_key=nid)
            before = deepcopy(meta)
            ng.relabel_script_frames_qc_chain(meta)
            for nid, variant in before["prompt_slot_variants"].items():
                if nid.startswith("n_cinema_"):
                    assert meta["prompt_slot_variants"][nid] == variant
    finally:
        await engine.dispose()
