"""Тесты graph planner."""

from __future__ import annotations

from app.models import NodeRunStatus, Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph


def test_default_graph_linear_next_after_plan_ready() -> None:
    g = WorkflowGraph.default()
    p = Project(topic="t", slug="t", status=ProjectStatus.plan_ready, meta={"graph_executor": True})
    nxt = g.next_running_after_ready(p, ProjectStatus.plan_ready)
    assert nxt == ProjectStatus.scripting


def test_disabled_script_skips_to_split() -> None:
    g = WorkflowGraph.default()
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.plan_ready,
        meta={"graph_executor": True, "disabled_nodes": ["n_script"]},
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
    p = Project(topic="t", slug="t", status=ProjectStatus.plan_ready, meta={"graph_executor": True})
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
    p = Project(topic="t", slug="t", status=ProjectStatus.plan_ready, meta={"graph_executor": True})
    assert g.next_running_after_ready(p, ProjectStatus.plan_ready) is None
    assert g.is_step_reachable(p, "script") is False


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
    p = Project(topic="t", slug="t", status=ProjectStatus.images_ready, meta={"graph_executor": True})
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
    p = Project(topic="t", slug="t", status=ProjectStatus.new, meta={"graph_executor": True})
    states = g.derived_node_states(p)
    assert states["n_script"] == NodeRunStatus.skipped
