"""Цепочка excel_gpt слотов: после #2 сразу #3, без зависания на ready."""

from __future__ import annotations

from types import SimpleNamespace

from app.models import Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph
from app.services.excel_gpt_node import (
    clear_excel_gpt_tail_completion,
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


def test_next_incomplete_force_rerun_when_chain_active() -> None:
    """Даже если слот 3 уже done — при chain_to=3 он в цепочке."""
    p = _project_with_excel_nodes([1, 2, 3], completed=[1, 2, 3])
    assert next_incomplete_excel_gpt_slot(p, 2) is None
    p.meta["enrich_auto_chain_to"] = 3
    assert next_incomplete_excel_gpt_slot(p, 2) == 3


def test_clear_tail_completion_allows_regen() -> None:
    p = _project_with_excel_nodes([1, 2, 3], completed=[1, 2, 3])
    cleared = clear_excel_gpt_tail_completion(p, from_slot=2)
    assert cleared["slots_cleared"] == [2, 3]
    assert set(cleared["keys_cleared"]) == {"n_excel_gpt_2", "n_excel_gpt_3"}
    assert p.meta["enrich_completed_slots"] == [1]
    assert p.meta["excel_gpt_completed_keys"] == ["n_excel_gpt_1"]


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


def test_enrich_ready_bypass_when_slot3_done_but_chain_active() -> None:
    p = _project_with_excel_nodes([1, 2, 3], completed=[1, 2, 3])
    p.status = ProjectStatus.enrich_2_ready
    p.meta["enrich_auto_chain_to"] = 3
    assert enrich_ready_bypasses_gen_queue(p) is True


def test_graph_next_reruns_done_excel_gpt_under_chain() -> None:
    """Planner не пропускает already-done excel_gpt #3 при активной цепочке."""
    nodes = [
        {"id": "n_split", "type": "split", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "n_excel_gpt_2",
            "type": "excel_gpt",
            "position": {"x": 100, "y": 0},
            "data": {"slotIndex": 2},
        },
        {
            "id": "n_excel_gpt_3",
            "type": "excel_gpt",
            "position": {"x": 200, "y": 0},
            "data": {"slotIndex": 3},
        },
        {"id": "n_hero", "type": "hero", "position": {"x": 300, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_split", "target": "n_excel_gpt_2"},
        {"id": "e2", "source": "n_excel_gpt_2", "target": "n_excel_gpt_3"},
        {"id": "e3", "source": "n_excel_gpt_3", "target": "n_hero"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.enrich_2_ready,
        meta={
            "split_completed": True,
            "enrich_completed_slots": [2, 3],
            "excel_gpt_completed_keys": ["n_excel_gpt_2", "n_excel_gpt_3"],
            "canvas_graph": {"nodes": nodes, "edges": edges},
        },
    )
    # Без force: слот 3 done → прыжок на hero
    assert g.next_running_after_ready(p, ProjectStatus.enrich_2_ready) is (
        ProjectStatus.generating_hero
    )
    p.meta["enrich_auto_chain_to"] = 3
    assert g.next_running_after_ready(p, ProjectStatus.enrich_2_ready) is (
        ProjectStatus.enriching_3
    )
