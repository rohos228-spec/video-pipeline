"""Тесты validate_workflow_graph."""

from __future__ import annotations

from app.orchestrator.graph.validate import validate_workflow_graph


def test_valid_linear_graph() -> None:
    nodes = [
        {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_script", "type": "script", "position": {"x": 1, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_plan", "target": "n_script", "sourceHandle": "out", "targetHandle": "in"},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is True
    assert r["errors"] == []


def test_detects_cycle() -> None:
    nodes = [
        {"id": "a", "type": "plan", "position": {}, "data": {}},
        {"id": "b", "type": "script", "position": {}, "data": {}},
        {"id": "c", "type": "split", "position": {}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "a", "target": "b"},
        {"id": "e2", "source": "b", "target": "c"},
        {"id": "e3", "source": "c", "target": "a"},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is False
    assert any("цикл" in e for e in r["errors"])


def test_broken_edge_reference() -> None:
    nodes = [{"id": "n_plan", "type": "plan", "position": {}, "data": {}}]
    edges = [{"id": "e1", "source": "n_plan", "target": "n_missing"}]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is False
