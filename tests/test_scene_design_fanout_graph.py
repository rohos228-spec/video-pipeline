"""Веер scene_design на канвасе: дефолтный граф + переходы планировщика."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.orchestrator.default_graph import SD_FANOUT, default_graph
from app.orchestrator.graph.planner import WorkflowGraph
from app.orchestrator.node_registry import (
    READY_TO_NODE_TYPE,
    RUNNING_TO_NODE_TYPE,
    SD_AGENT_STEP_CODES,
    STEP_CODE_TO_NODE_TYPE,
    spec_for_step_code,
    spec_for_type,
)


def _project(status: ProjectStatus) -> Project:
    return Project(topic="t", slug="sd-fanout", status=status, meta={})


def _fanout_graph() -> WorkflowGraph:
    """split → 5 marked excel_gpt → сборщик → excel_gpt_1 (как группа)."""
    nodes = [
        {"id": "n_split", "type": "split", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "n_excel_gpt_1",
            "type": "excel_gpt",
            "position": {"x": 3, "y": 0},
            "data": {"slotIndex": 1},
        },
    ]
    edges = []
    for i, (agent, _label, _descr) in enumerate(SD_FANOUT):
        nid = f"n_excel_gpt_sd_{agent}"
        nodes.append(
            {
                "id": nid,
                "type": "excel_gpt",
                "position": {"x": 1, "y": float(i)},
                "data": {"sd_agent": agent},
            }
        )
        edges.append({"id": f"e_s_{agent}", "source": "n_split", "target": nid})
        edges.append(
            {"id": f"e_{agent}_asm", "source": nid, "target": "n_excel_gpt_sd_asm"}
        )
    nodes.append(
        {
            "id": "n_excel_gpt_sd_asm",
            "type": "excel_gpt",
            "position": {"x": 2, "y": 0},
            "data": {"sd_agent": "assemble"},
        }
    )
    edges.append(
        {"id": "e_asm_x1", "source": "n_excel_gpt_sd_asm", "target": "n_excel_gpt_1"}
    )
    return WorkflowGraph(nodes, edges)


def test_default_graph_is_linear_without_scene_nodes() -> None:
    """Дефолт — чистый линейный пайплайн: веер только через «+ Группа»."""
    nodes, edges = default_graph()
    types = [str(n.get("type")) for n in nodes]
    assert "scene_design" not in types
    assert not any((n.get("data") or {}).get("sd_agent") for n in nodes)
    pairs = {(e["source"], e["target"]) for e in edges}
    assert ("n_split", "n_excel_gpt_1") in pairs
    assert ("n_excel_gpt_1", "n_hero") in pairs
    assert ("n_assemble", "n_publish") in pairs


def test_marked_excel_gpt_nodes_skip_slots() -> None:
    """Scene-агенты не занимают enrich-слоты и резолвятся как sd_* спеки."""
    from app.orchestrator.node_registry import spec_for_node
    from app.services.excel_gpt_node import (
        assign_slot_indices,
        effective_node_type,
        slot_index_from_node,
    )

    g = _fanout_graph()
    nodes = list(g._by_id.values())
    by_id = {n["id"]: n for n in nodes}
    cam = by_id["n_excel_gpt_sd_camera"]
    assert effective_node_type(cam) == "sd_agent"
    assert effective_node_type(by_id["n_excel_gpt_sd_asm"]) == "sd_assemble"
    assert slot_index_from_node(cam) == 0

    spec = spec_for_node(cam)
    assert spec is not None and spec.step_code == "scene_d"
    spec_asm = spec_for_node(by_id["n_excel_gpt_sd_asm"])
    assert spec_asm is not None and spec_asm.step_code == "scene_asm"
    # Обычная excel_gpt — по-прежнему enrich-слот.
    spec_x = spec_for_node(by_id["n_excel_gpt_1"])
    assert spec_x is not None and spec_x.step_code == "excel_gpt"

    assigned = assign_slot_indices(nodes)
    assigned_by_id = {n["id"]: n for n in assigned}
    assert "slotIndex" not in assigned_by_id["n_excel_gpt_sd_camera"]["data"]
    assert assigned_by_id["n_excel_gpt_1"]["data"]["slotIndex"] == 1


def test_registry_status_maps() -> None:
    assert RUNNING_TO_NODE_TYPE[ProjectStatus.scene_designing] == "sd_agent"
    assert RUNNING_TO_NODE_TYPE[ProjectStatus.scene_assembling] == "sd_assemble"
    assert READY_TO_NODE_TYPE[ProjectStatus.scene_agents_ready] == "sd_agent"
    assert READY_TO_NODE_TYPE[ProjectStatus.scene_design_ready] == "sd_assemble"

    spec = spec_for_type("sd_agent")
    assert spec is not None and spec.step_code == "scene_d"
    spec_asm = spec_for_type("sd_assemble")
    assert spec_asm is not None and spec_asm.step_code == "scene_asm"

    for code, agent in SD_AGENT_STEP_CODES.items():
        assert STEP_CODE_TO_NODE_TYPE[code] == "sd_agent"
        assert spec_for_step_code(code) is not None
        assert agent in ("skeleton", "characters", "world", "camera", "action")


def test_planner_frames_ready_starts_agents() -> None:
    g = _fanout_graph()
    found = g.next_work_node_after_ready(_project(ProjectStatus.frames_ready), ProjectStatus.frames_ready)
    assert found is not None
    key, running = found
    assert running is ProjectStatus.scene_designing
    assert g.node_type(key) == "sd_agent"


def test_planner_agents_ready_starts_assemble() -> None:
    g = _fanout_graph()
    found = g.next_work_node_after_ready(
        _project(ProjectStatus.scene_agents_ready), ProjectStatus.scene_agents_ready
    )
    assert found is not None
    key, running = found
    assert running is ProjectStatus.scene_assembling
    assert g.node_type(key) == "sd_assemble"


def test_planner_design_ready_continues_past_fanout() -> None:
    """После сборки пайплайн идёт дальше (excel_gpt #1 по цепочке)."""
    g = _fanout_graph()
    found = g.next_work_node_after_ready(
        _project(ProjectStatus.scene_design_ready), ProjectStatus.scene_design_ready
    )
    assert found is not None
    _key, running = found
    assert running is ProjectStatus.enriching_1


def test_planner_legacy_scene_design_counts_done() -> None:
    """Старый канвас (нода scene_design): после scene_design_ready она done,
    следующий шаг резолвится (hero по старой цепочке без excel-нод)."""
    nodes = [
        {"id": "n_split", "type": "split", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_scene_design", "type": "scene_design", "position": {"x": 1, "y": 0}, "data": {}},
        {"id": "n_hero", "type": "hero", "position": {"x": 2, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_split", "target": "n_scene_design"},
        {"id": "e2", "source": "n_scene_design", "target": "n_hero"},
    ]
    g = WorkflowGraph(nodes, edges)
    found = g.next_work_node_after_ready(
        _project(ProjectStatus.scene_design_ready), ProjectStatus.scene_design_ready
    )
    # READY_TO_NODE_TYPE[scene_design_ready]=sd_assemble — на старом канвасе
    # таких нод нет → None (auto_advance имеет линейный fallback на hero).
    assert found is None
    # Но done-вывод знает legacy-тип: hero достижим при проверке предков.
    done = g._work_types_done(_project(ProjectStatus.scene_design_ready))
    assert "scene_design" in done
