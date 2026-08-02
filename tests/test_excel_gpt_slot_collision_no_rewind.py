"""D9: несколько excel_gpt с одним slotIndex не откатывают пайплайн назад."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph
from app.orchestrator.node_registry import READY_TO_NODE_TYPE
from app.services.excel_gpt_node import EXCEL_GPT_NODE_TYPE


def _node(nid: str, typ: str, *, x: float = 0, slot: int | None = None) -> dict:
    data: dict = {"label": nid}
    if slot is not None:
        data["slotIndex"] = slot
    return {"id": nid, "type": typ, "position": {"x": x, "y": 0}, "data": data}


def _edge(src: str, tgt: str, kind: str = "after") -> dict:
    return {
        "id": f"e_{src}_{tgt}",
        "source": src,
        "target": tgt,
        "data": {"kind": kind},
    }


def test_hero_ready_does_not_skip_post_hero_slot5_to_image_prompts() -> None:
    """#50: hero → excel_gpt(slot5) → excel_gpt(slot5) → image_prompts.

    Stale enrich_completed_slots=[5] НЕ должен пропустить оба check и
    прыгнуть сразу в image_prompts.
    """
    nodes = [
        _node("n_hero", "hero", x=100),
        _node("n_excel_gpt_7", EXCEL_GPT_NODE_TYPE, x=200, slot=5),
        _node("n_excel_gpt_2", EXCEL_GPT_NODE_TYPE, x=300, slot=5),
        _node("n_image_prompts", "image_prompts", x=400),
        # «ранние» slot=5 до hero — уже пройдены
        _node("n_excel_gpt_1", EXCEL_GPT_NODE_TYPE, x=50, slot=5),
        _node("n_excel_gpt_6", EXCEL_GPT_NODE_TYPE, x=60, slot=5),
    ]
    edges = [
        _edge("n_excel_gpt_1", "n_excel_gpt_6"),
        _edge("n_excel_gpt_6", "n_hero"),
        _edge("n_hero", "n_excel_gpt_7"),
        _edge("n_excel_gpt_7", "n_excel_gpt_2"),
        _edge("n_excel_gpt_2", "n_image_prompts"),
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        id=50,
        slug="t",
        title="t",
        topic="t",
        status=ProjectStatus.hero_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "split_completed": True,
            "enrich_completed_slots": [3, 4, 5],
            "excel_gpt_completed_keys": [
                "n_excel_gpt_1",
                "n_excel_gpt_6",
            ],
        },
    )
    key = g.next_work_node_key_after_ready(p, ProjectStatus.hero_ready)
    assert key == "n_excel_gpt_7"
    running = g.next_running_after_ready(p, ProjectStatus.hero_ready)
    assert running is ProjectStatus.enriching_5


def test_images_ready_next_is_late_slot5_not_early() -> None:
    """После images следующая excel_gpt — check у кадров, не ранняя slot=5."""
    nodes = [
        _node("n_images", "images", x=100),
        _node("n_excel_early", EXCEL_GPT_NODE_TYPE, x=50, slot=5),
        _node("n_excel_late", EXCEL_GPT_NODE_TYPE, x=200, slot=5),
        _node("n_split", "split", x=60),
        _node("n_anim", "animation_prompts", x=300),
    ]
    edges = [
        _edge("n_excel_early", "n_split"),
        _edge("n_images", "n_excel_late"),
        _edge("n_excel_late", "n_anim"),
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        id=50,
        slug="t",
        title="t",
        topic="t",
        status=ProjectStatus.images_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            # Ранняя slot=5 уже пройдена по ключу, слот целиком НЕ done —
            # иначе BFS пропустит обе excel_gpt и сразу уйдёт в anim.
            "excel_gpt_completed_keys": ["n_excel_early"],
            "split_completed": True,
        },
    )
    key = g.next_work_node_key_after_ready(p, ProjectStatus.images_ready)
    assert key == "n_excel_late"
    running = g.next_running_after_ready(p, ProjectStatus.images_ready)
    assert running is ProjectStatus.enriching_5


def test_enrich5_ready_bfs_uses_active_key_not_all_slot5() -> None:
    """После enrich_5_ready BFS от active, а не от всех slot=5 → не split."""
    nodes = [
        _node("n_excel_early", EXCEL_GPT_NODE_TYPE, x=50, slot=5),
        _node("n_excel_late", EXCEL_GPT_NODE_TYPE, x=200, slot=5),
        _node("n_split", "split", x=60),
        _node("n_anim", "animation_prompts", x=300),
    ]
    edges = [
        _edge("n_excel_early", "n_split"),
        _edge("n_excel_late", "n_anim"),
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        id=50,
        slug="t",
        title="t",
        topic="t",
        status=ProjectStatus.enrich_5_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "active_excel_gpt_node_key": "n_excel_late",
            "excel_gpt_completed_keys": ["n_excel_late"],
            "enrich_completed_slots": [5],
        },
    )
    assert READY_TO_NODE_TYPE[ProjectStatus.enrich_5_ready].startswith("enrich_")
    key = g.next_work_node_key_after_ready(p, ProjectStatus.enrich_5_ready)
    assert key == "n_anim"
    running = g.next_running_after_ready(p, ProjectStatus.enrich_5_ready)
    assert running is ProjectStatus.generating_animation_prompts


def test_fail_retry_edge_does_not_block_first_audio() -> None:
    """D11: fail-стрелка check→audio не блокирует первый audio после videos."""
    nodes = [
        _node("n_videos", "videos", x=100),
        _node("n_check_vid", EXCEL_GPT_NODE_TYPE, x=200, slot=5),
        _node("n_audio", "audio", x=300),
        _node("n_check_audio", EXCEL_GPT_NODE_TYPE, x=400, slot=5),
    ]
    edges = [
        _edge("n_videos", "n_check_vid", "after"),
        _edge("n_check_vid", "n_audio", "after"),
        _edge("n_audio", "n_check_audio", "after"),
        _edge("n_check_audio", "n_audio", "fail"),
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        id=50,
        slug="t",
        title="t",
        topic="t",
        status=ProjectStatus.videos_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "enrich_completed_slots": [5],
            "excel_gpt_completed_keys": ["n_check_vid"],
            "split_completed": True,
        },
    )
    assert g.next_work_node_key_after_ready(p, ProjectStatus.videos_ready) == "n_audio"
