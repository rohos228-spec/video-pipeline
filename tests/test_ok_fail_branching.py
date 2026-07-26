"""Ветки ок / не ок: edge kinds pass/fail + planner + автоподпись роли."""

from __future__ import annotations

from pathlib import Path

from app.models import Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph
from app.services.gpt_operator import (
    gate_allows_successors,
    patch_operator_config,
    resolve_operator,
    save_operator_result,
    set_edge_kind_in_canvas,
    verdict_edge_blocks,
)


def _project(tmp_path: Path, monkeypatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="okfail",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={},
    )
    p.data_dir.mkdir(parents=True)
    return p


def test_pass_and_fail_edge_kinds(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    a, ok_n, fail_n = "gpt", "next_ok", "next_fail"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": a, "type": "excel_gpt", "position": {"x": 0, "y": 0}},
                {"id": ok_n, "type": "images", "position": {"x": 200, "y": 0}},
                {"id": fail_n, "type": "excel_gpt", "position": {"x": 200, "y": 100}},
            ],
            "edges": [
                {"id": "e_ok", "source": a, "target": ok_n, "data": {"kind": "after"}},
                {"id": "e_fail", "source": a, "target": fail_n, "data": {"kind": "after"}},
            ],
        },
        "excel_gpt_nodes": {a: {"role": "review", "transport": "api"}},
    }
    assert set_edge_kind_in_canvas(p, "e_ok", "pass")["data"]["kind"] == "pass"
    assert set_edge_kind_in_canvas(p, "e_fail", "fail")["data"]["kind"] == "fail"
    # legacy feed нормализуется в after
    p.meta["canvas_graph"]["edges"].append(
        {"id": "e_legacy", "source": a, "target": ok_n, "data": {"kind": "after"}}
    )
    assert set_edge_kind_in_canvas(p, "e_legacy", "feed")["data"]["kind"] == "after"

    up = p.data_dir / "excel_gpt_uploads" / a
    up.mkdir(parents=True)
    (up / "shot.png").write_bytes(b"\x89PNG" + b"x" * 200)
    p.meta["excel_gpt_nodes"][a]["uploadedFileNames"] = ["shot.png"]
    p.meta["excel_gpt_nodes"][a]["inputSource"] = "upload"

    res = resolve_operator(p, a)
    assert res["branching"]["enabled"] is True
    assert res["branching"]["hasPass"] is True
    assert res["branching"]["hasFail"] is True
    assert any(e["kind"] == "pass" for e in res["outgoingEdges"])
    assert any(e["kind"] == "fail" for e in res["outgoingEdges"])


def test_planner_pass_vs_fail_branches() -> None:
    nodes = [
        {"id": "gpt", "type": "excel_gpt", "data": {"slotIndex": 1}, "position": {"x": 0, "y": 0}},
        {"id": "img", "type": "images", "position": {"x": 200, "y": 0}},
        {"id": "fix", "type": "images", "position": {"x": 200, "y": 120}},
    ]
    edges = [
        {"id": "e_pass", "source": "gpt", "target": "img", "data": {"kind": "pass"}},
        {"id": "e_fail", "source": "gpt", "target": "fix", "data": {"kind": "fail"}},
    ]
    g = WorkflowGraph(nodes, edges)
    base_meta = {
        "excel_gpt_nodes": {"gpt": {"role": "review", "transport": "api"}},
        "excel_gpt_completed_keys": ["gpt"],
        "enrich_completed_slots": [1],
        "split_completed": True,
    }

    p_fail = Project(
        slug="bf",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        hero_mode="no_hero",
        meta={**base_meta, "gpt_operator_results": {"gpt": {"gateStatus": "fail"}}},
    )
    assert g.gate_blocks_edge(p_fail, "gpt", "img") is True
    assert g.gate_blocks_edge(p_fail, "gpt", "fix") is False

    p_pass = Project(
        slug="bp",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        hero_mode="no_hero",
        meta={**base_meta, "gpt_operator_results": {"gpt": {"gateStatus": "pass"}}},
    )
    assert g.gate_blocks_edge(p_pass, "gpt", "img") is False
    assert g.gate_blocks_edge(p_pass, "gpt", "fix") is True


def test_legacy_gate_kind_still_pass_only() -> None:
    nodes = [
        {"id": "gpt", "type": "excel_gpt", "data": {"slotIndex": 1}, "position": {"x": 0, "y": 0}},
        {"id": "img", "type": "images", "position": {"x": 200, "y": 0}},
    ]
    edges = [{"id": "e1", "source": "gpt", "target": "img", "data": {"kind": "gate"}}]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        slug="lg",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        hero_mode="no_hero",
        meta={
            "excel_gpt_nodes": {"gpt": {"role": "gate", "transport": "api"}},
            "gpt_operator_results": {"gpt": {"gateStatus": "pass"}},
        },
    )
    assert g.gate_blocks_edge(p, "gpt", "img") is False
    assert verdict_edge_blocks(p, "gpt", "gate") is False


def test_role_review_auto_label(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "n_excel_gpt_1"
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": key, "type": "excel_gpt", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
        "excel_gpt_nodes": {key: {"label": "Работа с GPT", "role": "assist"}},
    }
    res = patch_operator_config(p, key, {"role": "review", "transport": "api"})
    assert res["label"] == "Ок / не ок"
    assert p.meta["excel_gpt_nodes"][key]["label"] == "Ок / не ок"
    assert res["branching"]["enabled"] is True
    assert res["branching"]["hasFail"] is False


def test_review_verdict_opens_fail_branch(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "gpt"
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": key, "type": "excel_gpt", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
        "excel_gpt_nodes": {key: {"role": "review", "transport": "api"}},
    }
    assert gate_allows_successors(p, key) is None
    save_operator_result(
        p,
        key,
        input_paths=[],
        output_paths=[],
        reply_text="не ок",
        gate_status="fail",
    )
    assert gate_allows_successors(p, key) is False
    assert verdict_edge_blocks(p, key, "fail") is False
    assert verdict_edge_blocks(p, key, "pass") is True
