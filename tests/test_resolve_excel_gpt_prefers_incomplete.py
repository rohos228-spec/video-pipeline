"""resolve(slot) при коллизии slotIndex не должен брать уже done ноду."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    resolve_excel_gpt_node_key_for_slot,
)


def test_resolve_slot5_skips_completed_leftmost() -> None:
    nodes = [
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
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.hero_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": []},
            "excel_gpt_completed_keys": ["n_excel_gpt_1"],
        },
    )
    assert resolve_excel_gpt_node_key_for_slot(p, 5) == "n_excel_gpt_7"
