"""Planner: gate-стрелка блокирует следующий шаг при fail."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph


def test_gate_blocks_successor_when_fail() -> None:
    nodes = [
        {"id": "gpt", "type": "excel_gpt", "data": {"slotIndex": 1}, "position": {"x": 0, "y": 0}},
        {"id": "img", "type": "images", "position": {"x": 200, "y": 0}},
    ]
    edges = [
        {"id": "e1", "source": "gpt", "target": "img", "data": {"kind": "gate"}},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        slug="g",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        hero_mode="no_hero",
        meta={
            "excel_gpt_nodes": {"gpt": {"role": "gate", "transport": "api"}},
            "gpt_operator_results": {"gpt": {"gateStatus": "fail"}},
            "excel_gpt_completed_keys": ["gpt"],
            "enrich_completed_slots": [1],
            "split_completed": True,
        },
    )
    assert g.gate_blocks_edge(p, "gpt", "img") is True
    nxt = g.next_running_after_ready(p, ProjectStatus.enrich_1_ready)
    # images не должен стартовать при gate fail
    assert nxt is None or nxt.value != "generating_images"


def test_gate_allows_on_pass() -> None:
    nodes = [
        {"id": "gpt", "type": "excel_gpt", "data": {"slotIndex": 1}, "position": {"x": 0, "y": 0}},
        {"id": "img", "type": "images", "position": {"x": 200, "y": 0}},
    ]
    edges = [
        {"id": "e1", "source": "gpt", "target": "img", "data": {"kind": "gate"}},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        slug="g2",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        hero_mode="no_hero",
        meta={
            "excel_gpt_nodes": {"gpt": {"role": "gate", "transport": "api"}},
            "gpt_operator_results": {"gpt": {"gateStatus": "pass"}},
            "excel_gpt_completed_keys": ["gpt"],
            "enrich_completed_slots": [1],
            "split_completed": True,
        },
    )
    assert g.gate_blocks_edge(p, "gpt", "img") is False
