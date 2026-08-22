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


def test_ensure_enrich_auto_chain_to_sets_max_along_edges() -> None:
    p = _project_with_excel_nodes([1, 2, 3], completed=[1])
    # без рёбер — цепочки нет
    assert ensure_enrich_auto_chain_to(p, from_slot=1) is None
    nodes = p.meta["canvas_graph"]["nodes"]
    p.meta["canvas_graph"]["edges"] = [
        {"id": "e1", "source": "n_excel_gpt_1", "target": "n_excel_gpt_2"},
        {"id": "e2", "source": "n_excel_gpt_2", "target": "n_excel_gpt_3"},
    ]
    p.meta["canvas_graph"]["nodes"] = nodes
    assert ensure_enrich_auto_chain_to(p, from_slot=1) == 3
    assert p.meta["enrich_auto_chain_to"] == 3


def test_ensure_enrich_auto_chain_noop_when_last_slot() -> None:
    p = _project_with_excel_nodes([1, 2], completed=[1])
    p.meta["canvas_graph"]["edges"] = [
        {"id": "e1", "source": "n_excel_gpt_1", "target": "n_excel_gpt_2"},
    ]
    assert ensure_enrich_auto_chain_to(p, from_slot=2) is None
    assert "enrich_auto_chain_to" not in p.meta


def test_ensure_enrich_stops_at_script_edge() -> None:
    """GPT → script → GPT: после первого GPT цепочку не форсим через script."""
    nodes = [
        {
            "id": "n_excel_gpt_1",
            "type": "excel_gpt",
            "position": {"x": 0, "y": 0},
            "data": {"slotIndex": 1},
        },
        {"id": "n_script", "type": "script", "position": {"x": 100, "y": 0}, "data": {}},
        {
            "id": "n_excel_gpt_2",
            "type": "excel_gpt",
            "position": {"x": 200, "y": 0},
            "data": {"slotIndex": 2},
        },
    ]
    edges = [
        {"id": "e1", "source": "n_excel_gpt_1", "target": "n_script"},
        {"id": "e2", "source": "n_script", "target": "n_excel_gpt_2"},
    ]
    p = SimpleNamespace(
        meta={"canvas_graph": {"nodes": nodes, "edges": edges}},
        status=ProjectStatus.enrich_1_ready,
        id=1,
    )
    assert ensure_enrich_auto_chain_to(p, from_slot=1) is None
    from app.services.excel_gpt_node import prepare_enrich_chain_for_auto_advance

    assert prepare_enrich_chain_for_auto_advance(p, ProjectStatus.enrich_1_ready) is None


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
    """Planner не пропускает already-done excel_gpt #3 после enrich_2_ready."""
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
    # Stale «готово» на слоте 3 НЕ даёт прыжок на hero.
    assert g.next_running_after_ready(p, ProjectStatus.enrich_2_ready) is (
        ProjectStatus.enriching_3
    )
    p.meta["enrich_auto_chain_to"] = 3
    assert g.next_running_after_ready(p, ProjectStatus.enrich_2_ready) is (
        ProjectStatus.enriching_3
    )


def test_next_excel_slot_after_ready_ignores_done() -> None:
    from app.services.excel_gpt_node import (
        next_excel_gpt_running_after_ready,
        next_excel_gpt_slot_after_ready,
        prepare_enrich_chain_for_auto_advance,
    )

    p = _project_with_excel_nodes([1, 2, 3], completed=[1, 2, 3])
    p.status = ProjectStatus.enrich_2_ready
    p.meta["canvas_graph"]["edges"] = [
        {"id": "e2", "source": "n_excel_gpt_2", "target": "n_excel_gpt_3"},
    ]
    assert next_excel_gpt_slot_after_ready(p, ProjectStatus.enrich_2_ready) == 3
    assert next_excel_gpt_running_after_ready(p, ProjectStatus.enrich_2_ready) is (
        ProjectStatus.enriching_3
    )
    nxt = prepare_enrich_chain_for_auto_advance(p, ProjectStatus.enrich_2_ready)
    assert nxt is ProjectStatus.enriching_3
    assert p.meta.get("enrich_auto_chain_to") == 3
    assert 3 not in (p.meta.get("enrich_completed_slots") or [])
    assert "n_excel_gpt_3" not in (p.meta.get("excel_gpt_completed_keys") or [])


def test_graph_skips_to_hero_only_after_last_excel_slot() -> None:
    nodes = [
        {
            "id": "n_excel_gpt_2",
            "type": "excel_gpt",
            "position": {"x": 100, "y": 0},
            "data": {"slotIndex": 2},
        },
        {"id": "n_hero", "type": "hero", "position": {"x": 300, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e2", "source": "n_excel_gpt_2", "target": "n_hero"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.enrich_2_ready,
        meta={
            "enrich_completed_slots": [2],
            "excel_gpt_completed_keys": ["n_excel_gpt_2"],
            "canvas_graph": {"nodes": nodes, "edges": edges},
        },
    )
    assert g.next_running_after_ready(p, ProjectStatus.enrich_2_ready) is (
        ProjectStatus.generating_hero
    )


def test_prepare_chain_follows_edges_not_orphan_slots() -> None:
    """Слот 3 на канвасе, но рёбра excel_2→hero — идём по стрелке (не форсим #3)."""
    from app.services.excel_gpt_node import prepare_enrich_chain_for_auto_advance

    nodes = [
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
        {"id": "e2", "source": "n_excel_gpt_2", "target": "n_hero"},
    ]
    p = _project_with_excel_nodes([2, 3], completed=[2, 3])
    p.meta["canvas_graph"] = {"nodes": nodes, "edges": edges}
    p.status = ProjectStatus.enrich_2_ready
    assert prepare_enrich_chain_for_auto_advance(p, ProjectStatus.enrich_2_ready) is None


def test_auto_chain_uses_edge_key_not_slot_resolve_collision() -> None:
    """Баг #27: work→check при двух нодах со slot=5 — не прыгать на «персонажи».

    resolve(5) раньше брал первую ноду в списке (n_excel_gpt_2), хотя стрелка
    ведёт на check (checkMode). UI: проверка не «в работе»/не «готово»,
    стартуют персонажи через несколько нод.
    """
    from app.services.excel_gpt_node import (
        first_work_successor_along_edges,
        prepare_enrich_chain_for_auto_advance,
        resolve_excel_gpt_node_key_for_slot,
    )

    nodes = [
        {
            "id": "n_excel_gpt_2",
            "type": "excel_gpt",
            "position": {"x": 500, "y": 200},
            "data": {"slotIndex": 5, "label": "персонажи"},
        },
        {
            "id": "n_work",
            "type": "excel_gpt",
            "position": {"x": 1180, "y": 80},
            "data": {"slotIndex": 4, "label": "работа gpt"},
        },
        {
            "id": "n_check",
            "type": "excel_gpt",
            "position": {"x": 1300, "y": 400},
            "data": {"slotIndex": 5, "label": "проверка", "checkMode": True},
        },
        {"id": "n_hero", "type": "hero", "position": {"x": 1500, "y": 200}, "data": {}},
        {"id": "n_items", "type": "items", "position": {"x": 1700, "y": 200}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_work", "target": "n_check"},
        {"id": "e2", "source": "n_check", "target": "n_hero"},
        {"id": "e3", "source": "n_hero", "target": "n_items"},
        {"id": "e4", "source": "n_items", "target": "n_excel_gpt_2"},
    ]
    p = SimpleNamespace(
        id=27,
        status=ProjectStatus.enrich_4_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "excel_gpt_completed_keys": ["n_work"],
            "enrich_completed_slots": [4],
            "node_step_params": {
                "n_check": {"checkMode": True},
            },
        },
        auto_mode=True,
    )
    # Коллизия slot 5: по стрелке следующий work — check, не orphan персонажи.
    assert resolve_excel_gpt_node_key_for_slot(p, 5) == "n_check"
    assert first_work_successor_along_edges(p, "n_work") == ("n_check", "excel_gpt")

    nxt = prepare_enrich_chain_for_auto_advance(
        p, ProjectStatus.enrich_4_ready, finished_key="n_work"
    )
    assert nxt is ProjectStatus.enriching_5
    assert p.meta["active_excel_gpt_node_key"] == "n_check"
    assert p.meta["active_excel_gpt_node_key"] != "n_excel_gpt_2"
