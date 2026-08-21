"""Сверка оператора GPT: файлы + стрелки, без браузера."""

from __future__ import annotations

from pathlib import Path

from app.models import Project, ProjectStatus
from app.services.gpt_operator import (
    gate_allows_successors,
    patch_operator_config,
    resolve_operator,
    save_operator_result,
    set_edge_kind_in_canvas,
)
from app.services.gpt_operator_client import run_operator_api


def _project(tmp_path: Path, monkeypatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="op-test",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={},
    )
    p.data_dir.mkdir(parents=True)
    return p


def test_resolve_manual_upload_and_missing_file(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "n_excel_gpt_1"
    up = p.data_dir / "excel_gpt_uploads" / key
    up.mkdir(parents=True)
    (up / "a.png").write_bytes(b"\x89PNG" + b"0" * 300)
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": key, "type": "excel_gpt", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
        "excel_gpt_nodes": {
            key: {
                "role": "review",
                "transport": "api",
                "outputMode": "text",
                "uploadedFileNames": ["a.png", "missing.bin"],
                "inputSource": "upload",
            }
        },
    }
    res = resolve_operator(p, key)
    assert res["role"] == "review"
    assert res["okFileCount"] == 1
    assert any(not f["ok"] for f in res["files"])
    assert res["consistent"] is False


def test_incoming_edge_pulls_scene_images(tmp_path: Path, monkeypatch) -> None:
    """Любая связь + takeFromEdges (default) — вход с прошлой ноды."""
    p = _project(tmp_path, monkeypatch)
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "f001.png").write_bytes(b"\x89PNG" + b"1" * 400)
    (scenes / "f002.png").write_bytes(b"\x89PNG" + b"2" * 400)
    gpt = "n_excel_gpt_1"
    img = "n_images"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": img, "type": "images", "position": {"x": 0, "y": 0}},
                {"id": gpt, "type": "excel_gpt", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": img,
                    "target": gpt,
                    "data": {"kind": "after"},
                }
            ],
        },
        "excel_gpt_nodes": {
            gpt: {"role": "review", "transport": "api", "outputMode": "text"}
        },
    }
    res = resolve_operator(p, gpt)
    assert res["canRun"] is True
    assert res["takeFromEdges"] is True
    assert res["okFileCount"] == 2
    kinds = {e["kind"] for e in res["incomingEdges"]}
    assert "after" in kinds
    assert all(f["origin"] in ("edge", "snapshot") for f in res["files"] if f["ok"])


def test_project_file_does_not_inject_xlsx(
    tmp_path: Path, monkeypatch
) -> None:
    """DB SoT: project_file не тащит Excel — canRun без xlsx (db_frames на run)."""
    p = _project(tmp_path, monkeypatch)
    check = "n_check"
    next_gpt = "n_excel_gpt_2"
    (p.data_dir / "project.xlsx").write_bytes(b"PK" + b"0" * 200)
    up = p.data_dir / "excel_gpt_uploads" / check
    up.mkdir(parents=True)
    report = up / "check_report.txt"
    report.write_text("# ОТЧЁТ\nverdict: pass\n", encoding="utf-8")
    reply = up / "gpt_reply.txt"
    reply.write_text("ok", encoding="utf-8")
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": check, "type": "excel_gpt", "position": {"x": 0, "y": 0}},
                {"id": next_gpt, "type": "excel_gpt", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {
                    "id": "e_ok",
                    "source": check,
                    "target": next_gpt,
                    "data": {"kind": "ok"},
                }
            ],
        },
        "excel_gpt_nodes": {
            check: {
                "role": "review",
                "checkMode": True,
                "emitKinds": ["reply_txt"],
                "transport": "api",
            },
            next_gpt: {
                "role": "assist",
                "outputMode": "project_file",
                "transport": "api",
                "takeFromEdges": True,
            },
        },
        "gpt_operator_results": {
            check: {
                "gateStatus": "pass",
                "outputPaths": [str(report), str(reply)],
                "inputPaths": [],
            }
        },
    }
    res = resolve_operator(p, next_gpt)
    names = {str(f.get("name") or "") for f in res["files"] if f.get("ok")}
    assert "project.xlsx" not in names
    assert res["canRun"] is True


def test_take_from_edges_off_skips_incoming(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "f001.png").write_bytes(b"\x89PNG" + b"1" * 400)
    gpt = "n_excel_gpt_1"
    img = "n_images"
    up = p.data_dir / "excel_gpt_uploads" / gpt
    up.mkdir(parents=True)
    (up / "manual.txt").write_text("own", encoding="utf-8")
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": img, "type": "images", "position": {"x": 0, "y": 0}},
                {"id": gpt, "type": "excel_gpt", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": img, "target": gpt, "data": {"kind": "after"}},
            ],
        },
        "excel_gpt_nodes": {
            gpt: {
                "role": "assist",
                "transport": "api",
                "takeFromEdges": False,
                "uploadedFileNames": ["manual.txt"],
                "inputSource": "upload",
            }
        },
    }
    res = resolve_operator(p, gpt)
    assert res["takeFromEdges"] is False
    assert res["okFileCount"] == 1
    assert all(f.get("fromNode") in (None, "") for f in res["files"] if f["ok"])
    assert any("выключен" in w for w in res["warnings"])


def test_compare_needs_two_files(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "n_excel_gpt_1"
    up = p.data_dir / "excel_gpt_uploads" / key
    up.mkdir(parents=True)
    (up / "only.txt").write_text("x", encoding="utf-8")
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": key, "type": "excel_gpt", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
        "excel_gpt_nodes": {
            key: {
                "role": "compare",
                "transport": "api",
                "uploadedFileNames": ["only.txt"],
            }
        },
    }
    res = resolve_operator(p, key)
    assert res["canRun"] is False
    assert any("сравнить" in e for e in res["errors"])


def test_set_edge_kind_and_gate_result(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    a, b = "n_a", "n_b"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": a, "type": "excel_gpt", "position": {"x": 0, "y": 0}},
                {"id": b, "type": "images", "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "e_ab", "source": a, "target": b, "data": {"kind": "after"}}],
        },
        "excel_gpt_nodes": {a: {"role": "gate", "transport": "api"}},
    }
    updated = set_edge_kind_in_canvas(p, "e_ab", "gate")
    assert updated is not None
    assert updated["data"]["kind"] == "gate"
    assert gate_allows_successors(p, a) is None
    save_operator_result(
        p,
        a,
        input_paths=[],
        output_paths=[p.data_dir / "excel_gpt_uploads" / a / "gpt_reply.txt"],
        reply_text="ok",
        gate_status="fail",
    )
    (p.data_dir / "excel_gpt_uploads" / a).mkdir(parents=True, exist_ok=True)
    assert gate_allows_successors(p, a) is False


def test_api_stub_writes_reply(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: False)
    p = _project(tmp_path, monkeypatch)
    key = "n_excel_gpt_1"
    src = p.data_dir / "project.xlsx"
    src.write_bytes(b"PK" + b"0" * 200)

    res = asyncio.run(
        run_operator_api(
            project_dir=p.data_dir,
            node_key=key,
            role="review",
            output_mode="text",
            prompt="check",
            accompanying="go",
            input_paths=[src],
        )
    )
    assert res.output_paths
    assert any(p.name == "analysis.json" for p in res.output_paths)
    assert "vp.check.v1" in res.reply_text or "stub" in res.reply_text.lower()
    assert res.gate_status == "pass"
    assert res.analysis is not None
    assert res.analysis.verdict == "pass"
    analysis_path = next(p for p in res.output_paths if p.name == "analysis.json")
    assert "pass" in analysis_path.read_text(encoding="utf-8")


def test_patch_sets_transport_api(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "n_excel_gpt_1"
    (p.data_dir / "project.xlsx").write_bytes(b"PK" + b"0" * 200)
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": key, "type": "excel_gpt", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
        "excel_gpt_nodes": {key: {}},
    }
    res = patch_operator_config(p, key, {"role": "transform", "outputMode": "sidecar"})
    assert res["role"] == "transform"
    assert res["config"]["transport"] == "api"
    assert res["outputMode"] == "sidecar"


def test_resolve_hydrates_assist_reply_from_disk(tmp_path: Path, monkeypatch) -> None:
    """meta.gpt_operator_results стёрты — UI всё равно видит gpt_reply.txt."""
    p = _project(tmp_path, monkeypatch)
    key = "n_excel_gpt_2"
    (p.data_dir / "project.xlsx").write_bytes(b"PK" + b"0" * 200)
    up = p.data_dir / "excel_gpt_uploads" / key
    up.mkdir(parents=True)
    (up / "gpt_reply.txt").write_text(
        '{"ops":[{"frame_uuid":"abc","fields":{"место":"двор"}}]}\n',
        encoding="utf-8",
    )
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": key, "type": "excel_gpt", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
        "excel_gpt_nodes": {
            key: {"role": "assist", "outputMode": "project_file", "transport": "api"}
        },
        # как после canvas PATCH / clear_step
        "gpt_operator_results": {},
    }
    res = resolve_operator(p, key)
    last = res.get("lastResult") or {}
    assert last.get("replyPreview")
    assert "frame_uuid" in str(last.get("replyPreview"))
    assert any(
        str(p).endswith("gpt_reply.txt") for p in (last.get("outputPaths") or [])
    )
