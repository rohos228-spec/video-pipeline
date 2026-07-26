"""Browser/API: apply_check_reply пишет analysis.json и открывает ветки."""

from __future__ import annotations

import json
from pathlib import Path

from app.models import Project, ProjectStatus
from app.services.check_analysis import SCHEMA_ID
from app.services.gpt_operator import (
    apply_check_reply,
    gate_allows_successors,
    resolve_operator,
    verdict_edge_blocks,
)


def _project(tmp_path: Path, monkeypatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="checkreply",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={
            "canvas_graph": {
                "nodes": [
                    {"id": "n_check", "type": "excel_gpt", "position": {"x": 0, "y": 0}},
                    {"id": "n_next", "type": "images", "position": {"x": 1, "y": 0}},
                ],
                "edges": [
                    {
                        "id": "e_ok",
                        "source": "n_check",
                        "target": "n_next",
                        "data": {"kind": "pass"},
                    },
                    {
                        "id": "e_fail",
                        "source": "n_check",
                        "target": "n_next",
                        "data": {"kind": "fail"},
                    },
                ],
            },
            "excel_gpt_nodes": {"n_check": {"role": "review", "transport": "browser"}},
        },
    )
    p.data_dir.mkdir(parents=True)
    (p.data_dir / "project.xlsx").write_bytes(b"PK" + b"0" * 200)
    return p


def test_apply_check_reply_pass_opens_ok_edge(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    reply = json.dumps(
        {
            "schema": SCHEMA_ID,
            "verdict": "pass",
            "summary": "ок",
            "checks": [{"id": "x", "ok": True}],
            "forward": {"mode": "inherit", "paths": []},
            "fix": {"target": "none", "instructions": ""},
        },
        ensure_ascii=False,
    )
    entry = apply_check_reply(
        p,
        "n_check",
        reply,
        input_paths=[p.data_dir / "project.xlsx"],
    )
    assert entry["gateStatus"] == "pass"
    assert (p.data_dir / "excel_gpt_uploads" / "n_check" / "analysis.json").is_file()
    assert gate_allows_successors(p, "n_check") is True
    assert verdict_edge_blocks(p, "n_check", "pass") is False
    assert verdict_edge_blocks(p, "n_check", "fail") is True


def test_apply_check_reply_broken_json_is_fail(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    entry = apply_check_reply(p, "n_check", "нет json", input_paths=[])
    assert entry["gateStatus"] == "fail"
    assert gate_allows_successors(p, "n_check") is False
    assert verdict_edge_blocks(p, "n_check", "fail") is False
    assert verdict_edge_blocks(p, "n_check", "pass") is True


def test_resolve_exposes_analysis(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    apply_check_reply(
        p,
        "n_check",
        json.dumps(
            {
                "schema": SCHEMA_ID,
                "verdict": "fail",
                "summary": "нужна правка кадра 3",
                "checks": [{"id": "frame_3", "ok": False, "note": "артефакт"}],
                "forward": {"mode": "inherit", "paths": []},
                "fix": {"target": "source", "instructions": "переген кадр 3"},
            },
            ensure_ascii=False,
        ),
        input_paths=[p.data_dir / "project.xlsx"],
    )
    res = resolve_operator(p, "n_check")
    assert res["analysis"] is not None
    assert res["analysis"]["verdict"] == "fail"
    assert res["analysis"]["summary"] == "нужна правка кадра 3"
    assert res["branching"]["verdict"] == "fail"
    assert res["analysis"]["checks"][0]["id"] == "frame_3"
