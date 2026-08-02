"""resolve(slot) при коллизии slotIndex идёт по стрелкам, не leftmost."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    resolve_excel_gpt_node_key_for_slot,
)


def test_resolve_slot5_follows_graph_after_hero() -> None:
    nodes = [
        {"id": "n_hero", "type": "hero", "position": {"x": 100, "y": 0}, "data": {}},
        {
            "id": "n_excel_gpt_1",
            "type": EXCEL_GPT_NODE_TYPE,
            "position": {"x": 50, "y": 0},
            "data": {"slotIndex": 5},
        },
        {
            "id": "n_excel_gpt_7",
            "type": EXCEL_GPT_NODE_TYPE,
            "position": {"x": 200, "y": 0},
            "data": {"slotIndex": 5},
        },
        {
            "id": "n_excel_gpt_2",
            "type": EXCEL_GPT_NODE_TYPE,
            "position": {"x": 300, "y": 0},
            "data": {"slotIndex": 5},
        },
    ]
    edges = [
        {
            "id": "e1",
            "source": "n_hero",
            "target": "n_excel_gpt_7",
            "data": {"kind": "after"},
        },
        {
            "id": "e2",
            "source": "n_excel_gpt_7",
            "target": "n_excel_gpt_2",
            "data": {"kind": "after"},
        },
    ]
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.hero_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            # Ранний ключ НЕ в completed — leftmost всё равно не должен победить.
            "excel_gpt_completed_keys": [],
        },
    )
    assert resolve_excel_gpt_node_key_for_slot(p, 5) == "n_excel_gpt_7"
