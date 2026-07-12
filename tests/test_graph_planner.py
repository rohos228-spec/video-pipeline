"""Тесты graph planner."""

from __future__ import annotations

from app.models import NodeRunStatus, Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph


def test_default_graph_linear_next_after_plan_ready() -> None:
    g = WorkflowGraph.default()
    p = Project(topic="t", slug="t", status=ProjectStatus.plan_ready, meta={})
    nxt = g.next_running_after_ready(p, ProjectStatus.plan_ready)
    assert nxt == ProjectStatus.scripting


def test_disabled_script_skips_to_split() -> None:
    g = WorkflowGraph.default()
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.plan_ready,
        meta={"disabled_nodes": ["n_script"]},
    )
    nxt = g.next_running_after_ready(p, ProjectStatus.plan_ready)
    assert nxt == ProjectStatus.splitting


def test_derived_states_marks_disabled_skipped() -> None:
    g = WorkflowGraph.default()
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.plan_ready,
        meta={"disabled_nodes": ["n_script"]},
    )
    states = g.derived_node_states(p)
    assert states.get("n_script") == NodeRunStatus.skipped


def test_custom_edge_bypass() -> None:
    """plan → images напрямую (минуя script/split/...)."""
    nodes = [
        {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_images", "type": "images", "position": {"x": 300, "y": 0}, "data": {}},
    ]
    edges = [
        {
            "id": "e1",
            "source": "n_plan",
            "target": "n_images",
            "sourceHandle": "out",
            "targetHandle": "in",
        }
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(topic="t", slug="t", status=ProjectStatus.plan_ready, meta={})
    nxt = g.next_running_after_ready(p, ProjectStatus.plan_ready)
    assert nxt == ProjectStatus.generating_images


def test_disconnected_graph_returns_none() -> None:
    nodes = [
        {"id": "n_topic", "type": "topic", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_plan", "type": "plan", "position": {"x": 100, "y": 0}, "data": {}},
        {"id": "n_script", "type": "script", "position": {"x": 200, "y": 0}, "data": {}},
    ]
    edges = [
        {
            "id": "e_topic_plan",
            "source": "n_topic",
            "target": "n_plan",
            "sourceHandle": "out",
            "targetHandle": "in",
        }
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(topic="t", slug="t", status=ProjectStatus.plan_ready, meta={})
    assert g.next_running_after_ready(p, ProjectStatus.plan_ready) is None
    # script изолирован — автопродвижение не пойдёт, но ручной запуск возможен по linear prereq
    assert g.is_step_reachable(p, "script") is True
    p_new = Project(topic="t", slug="t", status=ProjectStatus.new, meta={})
    assert g.is_step_reachable(p_new, "script") is False


def test_excel_gpt_predecessor_allows_hero() -> None:
    """После enrich_1 hero достижим, если excel_gpt — предшественник по графу."""
    nodes = [
        {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "n_excel_gpt_1",
            "type": "excel_gpt",
            "position": {"x": 100, "y": 0},
            "data": {"slotIndex": 1},
        },
        {"id": "n_hero", "type": "hero", "position": {"x": 200, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_plan", "target": "n_excel_gpt_1", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e2", "source": "n_excel_gpt_1", "target": "n_hero", "sourceHandle": "out", "targetHandle": "in"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.enrich_1_ready,
        meta={"enrich_completed_slots": [1]},
    )
    assert g.is_step_reachable(p, "hero") is True
    nxt = g.next_running_after_ready(p, ProjectStatus.enrich_1_ready)
    assert nxt == ProjectStatus.generating_hero


def test_orphan_plan_in_flow_without_topic_edge() -> None:
    """plan→script без связи topic→plan — обе ноды в потоке."""
    nodes = [
        {"id": "n_topic", "type": "topic", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_plan", "type": "plan", "position": {"x": 100, "y": 0}, "data": {}},
        {"id": "n_script", "type": "script", "position": {"x": 200, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_plan", "target": "n_script", "sourceHandle": "out", "targetHandle": "in"},
    ]
    g = WorkflowGraph(nodes, edges)
    flow = g._flow_work_keys(set())
    assert "n_plan" in flow
    assert "n_script" in flow
    p = Project(topic="t", slug="t", status=ProjectStatus.new, meta={})
    assert g.is_step_reachable(p, "plan") is True


def test_bypass_graph_done_types_exclude_skipped_linear_steps() -> None:
    nodes = [
        {"id": "n_topic", "type": "topic", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_plan", "type": "plan", "position": {"x": 100, "y": 0}, "data": {}},
        {"id": "n_images", "type": "images", "position": {"x": 300, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_topic", "target": "n_plan", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e2", "source": "n_plan", "target": "n_images", "sourceHandle": "out", "targetHandle": "in"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(topic="t", slug="t", status=ProjectStatus.images_ready, meta={})
    done = g._work_types_done(p)
    assert "plan" in done
    assert "images" in done
    assert "script" not in done
    assert "split" not in done


def test_isolated_work_node_marked_skipped_in_derived_states() -> None:
    nodes = [
        {"id": "n_topic", "type": "topic", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_plan", "type": "plan", "position": {"x": 100, "y": 0}, "data": {}},
        {"id": "n_script", "type": "script", "position": {"x": 200, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_topic", "target": "n_plan", "sourceHandle": "out", "targetHandle": "in"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(topic="t", slug="t", status=ProjectStatus.new, meta={})
    states = g.derived_node_states(p)
    # script без связи с plan — входная нода, не skipped
    assert states["n_script"] == NodeRunStatus.pending
