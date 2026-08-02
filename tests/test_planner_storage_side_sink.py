"""Рёбра work→storage не ломают порядок следующего шага по цепочке."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph


def test_next_after_split_ignores_storage_fan_in() -> None:
    nodes = [
        {"id": "n_split", "type": "split", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "n_eg1",
            "type": "excel_gpt",
            "position": {"x": 100, "y": 0},
            "data": {"slotIndex": 1},
        },
        {"id": "n_hero", "type": "hero", "position": {"x": 200, "y": 0}, "data": {}},
        {
            "id": "n_storage_1",
            "type": "storage",
            "position": {"x": 0, "y": 200},
            "data": {"label": "Хранилище"},
        },
    ]
    edges = [
        {"id": "e0", "source": "n_split", "target": "n_eg1"},
        {"id": "e1", "source": "n_eg1", "target": "n_hero"},
        # fan-in как после connect_edges from=each
        {"id": "es0", "source": "n_split", "target": "n_storage_1"},
        {"id": "es1", "source": "n_eg1", "target": "n_storage_1"},
        {"id": "es2", "source": "n_hero", "target": "n_storage_1"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        id=1,
        slug="t",
        topic="t",
        status=ProjectStatus.frames_ready,
        meta={"split_completed": True},
    )
    nxt = g.next_work_node_after_ready(p, ProjectStatus.frames_ready)
    assert nxt is not None
    assert nxt[0] == "n_eg1"
    # predecessors excel_gpt — только split, не весь граф через storage
    preds = g._effective_predecessors("n_eg1", set())
    assert preds == {"n_split"}
