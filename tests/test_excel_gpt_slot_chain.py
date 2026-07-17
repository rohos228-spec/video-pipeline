"""Цепочка excel_gpt слотов: после #2 сразу #3, без зависания на ready."""

from __future__ import annotations

from types import SimpleNamespace

from app.models import ProjectStatus
from app.services.excel_gpt_node import (
    ensure_enrich_auto_chain_to,
    max_excel_gpt_slot,
    next_incomplete_excel_gpt_slot,
)
from app.services.gen_queue import enrich_ready_bypasses_gen_queue


def _project_with_excel_nodes(slots: list[int], *, completed: list[int] | None = None):
    nodes = [
        {
            "id": f"n_excel_gpt_{s}",
            "type": "excel_gpt",
            "position": {"x": s * 100, "y": 0},
            "data": {"slotIndex": s, "label": "Работа с GPT"},
        }
        for s in slots
    ]
    meta: dict = {
        "canvas_graph": {"nodes": nodes, "edges": []},
    }
    if completed:
        meta["enrich_completed_slots"] = list(completed)
        meta["excel_gpt_completed_keys"] = [f"n_excel_gpt_{s}" for s in completed]
    return SimpleNamespace(
        meta=meta,
        status=ProjectStatus.enrich_2_ready,
        id=42,
    )


def test_max_excel_gpt_slot_from_canvas() -> None:
    p = _project_with_excel_nodes([1, 2, 3])
    assert max_excel_gpt_slot(p) == 3


def test_next_incomplete_skips_done_slots() -> None:
    p = _project_with_excel_nodes([1, 2, 3], completed=[1, 2])
    assert next_incomplete_excel_gpt_slot(p, 2) == 3
    assert next_incomplete_excel_gpt_slot(p, 3) is None


def test_ensure_enrich_auto_chain_to_sets_max() -> None:
    p = _project_with_excel_nodes([1, 2, 3], completed=[1])
    assert ensure_enrich_auto_chain_to(p, from_slot=2) == 3
    assert p.meta["enrich_auto_chain_to"] == 3


def test_ensure_enrich_auto_chain_noop_when_last_slot() -> None:
    p = _project_with_excel_nodes([1, 2], completed=[1])
    assert ensure_enrich_auto_chain_to(p, from_slot=2) is None
    assert "enrich_auto_chain_to" not in p.meta


def test_enrich_ready_bypasses_gen_queue_when_slot3_pending() -> None:
    p = _project_with_excel_nodes([1, 2, 3], completed=[1, 2])
    p.status = ProjectStatus.enrich_2_ready
    assert enrich_ready_bypasses_gen_queue(p) is True


def test_enrich_ready_no_bypass_when_chain_done() -> None:
    p = _project_with_excel_nodes([1, 2, 3], completed=[1, 2, 3])
    p.status = ProjectStatus.enrich_3_ready
    assert enrich_ready_bypasses_gen_queue(p) is False
