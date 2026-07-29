"""forwardPaths / inherit / emitKinds: какие файлы отдаёт нода дальше."""

from __future__ import annotations

from pathlib import Path

from app.models import Project, ProjectStatus
from app.services.gpt_operator import (
    apply_check_reply,
    files_from_source_node,
    patch_operator_config,
    save_operator_result,
)


def _project(tmp_path: Path, monkeypatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="fwd",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={
            "canvas_graph": {
                "nodes": [
                    {"id": "n_check", "type": "excel_gpt", "position": {"x": 0, "y": 0}},
                ],
                "edges": [],
            },
            "excel_gpt_nodes": {"n_check": {"role": "review"}},
        },
    )
    p.data_dir.mkdir(parents=True)
    return p


def test_inherit_forwards_input_not_analysis(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    scene = p.data_dir / "scenes"
    scene.mkdir()
    img = scene / "frame_001.png"
    img.write_bytes(b"\x89PNG" + b"0" * 100)

    apply_check_reply(
        p,
        "n_check",
        '{"schema":"vp.check.v1","verdict":"pass","summary":"ok","checks":[],'
        '"forward":{"mode":"inherit","paths":[]},"fix":{"target":"none"}}',
        input_paths=[img],
    )
    got = files_from_source_node(p, "n_check")
    assert img in got
    # дефолт review: inputs + reply_txt — analysis не уходит
    assert any(x.name == "gpt_reply.txt" for x in got)
    assert not any(x.name == "analysis.json" for x in got)


def test_rewrite_plus_report_emit(tmp_path: Path, monkeypatch) -> None:
    """rewrite_file + emitKinds reply_txt → исправленный файл И отчёт."""
    p = _project(tmp_path, monkeypatch)
    orig = p.data_dir / "project.xlsx"
    orig.write_bytes(b"PK" + b"0" * 100)
    fixed_dir = p.data_dir / "old"
    fixed_dir.mkdir()
    fixed_file = fixed_dir / "project_fixed.xlsx"
    fixed_file.write_bytes(b"PK" + b"1" * 100)

    apply_check_reply(
        p,
        "n_check",
        '{"schema":"vp.check.v1","verdict":"pass","summary":"поправил",'
        '"checks":[],"forward":{"mode":"inherit","paths":[]},'
        '"fix":{"target":"xlsx","instructions":"ok",'
        '"rewrite_file":"old/project_fixed.xlsx"}}',
        input_paths=[orig],
    )
    patch_operator_config(
        p, "n_check", {"emitKinds": ["inputs", "reply_txt"], "role": "review"}
    )
    got = files_from_source_node(p, "n_check")
    names = {x.name for x in got}
    assert "project_fixed.xlsx" in names
    assert "gpt_reply.txt" in names
    assert "analysis.json" not in names


def test_explicit_forward_paths(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    a = p.data_dir / "characters"
    a.mkdir()
    keep = a / "hero.png"
    keep.write_bytes(b"\x89PNG" + b"0" * 80)
    other = a / "skip.png"
    other.write_bytes(b"\x89PNG" + b"0" * 80)

    apply_check_reply(
        p,
        "n_check",
        '{"schema":"vp.check.v1","verdict":"pass","summary":"ok","checks":[],'
        '"forward":{"mode":"explicit","paths":["characters/hero.png"]},'
        '"fix":{"target":"none"}}',
        input_paths=[keep, other],
    )
    got = files_from_source_node(p, "n_check")
    assert got == [keep]


def test_fix_rewrite_file_forwarded(tmp_path: Path, monkeypatch) -> None:
    """Агент сам исправил файл (fix.rewrite_file) → дальше идёт исправленная версия."""
    p = _project(tmp_path, monkeypatch)
    orig = p.data_dir / "project.xlsx"
    orig.write_bytes(b"PK" + b"0" * 100)
    fixed = p.data_dir / "old"
    fixed.mkdir()
    fixed_file = fixed / "project_fixed.xlsx"
    fixed_file.write_bytes(b"PK" + b"1" * 100)

    apply_check_reply(
        p,
        "n_check",
        '{"schema":"vp.check.v1","verdict":"pass","summary":"исправил",'
        '"checks":[{"id":"task_applied","ok":true}],'
        '"forward":{"mode":"inherit","paths":[]},'
        '"fix":{"target":"xlsx","instructions":"поправил R48",'
        '"rewrite_file":"old/project_fixed.xlsx"}}',
        input_paths=[orig],
    )
    got = files_from_source_node(p, "n_check")
    assert fixed_file in got  # исправленный файл имеет приоритет над inherit
    assert any(x.name == "gpt_reply.txt" for x in got)  # отчёт тоже (дефолт emit)


def test_emit_kinds_reply_and_analysis(tmp_path: Path, monkeypatch) -> None:
    """Выбор «что отдаёт»: текст .txt + analysis.json."""
    p = _project(tmp_path, monkeypatch)
    xlsx = p.data_dir / "project.xlsx"
    xlsx.write_bytes(b"PK" + b"0" * 80)
    up = p.data_dir / "excel_gpt_uploads" / "n_check"
    up.mkdir(parents=True)
    reply = up / "gpt_reply.txt"
    reply.write_text("ok reply\n", encoding="utf-8")
    analysis = up / "analysis.json"
    analysis.write_text('{"schema":"vp.check.v1","verdict":"pass"}', encoding="utf-8")

    save_operator_result(
        p,
        "n_check",
        input_paths=[xlsx],
        output_paths=[analysis, reply],
        reply_text="ok reply",
        gate_status="pass",
        analysis={
            "schema": "vp.check.v1",
            "verdict": "pass",
            "summary": "ok",
            "checks": [],
            "forward": {"mode": "inherit", "paths": []},
            "fix": {"target": "none"},
        },
    )
    patch_operator_config(
        p, "n_check", {"emitKinds": ["reply_txt", "analysis"], "role": "review"}
    )
    got = files_from_source_node(p, "n_check")
    names = {p.name for p in got}
    assert names == {"gpt_reply.txt", "analysis.json"}
    assert xlsx not in got


def test_emit_kinds_result_and_inputs(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    p.meta["excel_gpt_nodes"]["n_check"]["role"] = "assist"
    xlsx = p.data_dir / "project.xlsx"
    xlsx.write_bytes(b"PK" + b"0" * 80)
    img = p.data_dir / "scenes"
    img.mkdir()
    frame = img / "a.png"
    frame.write_bytes(b"\x89PNG" + b"0" * 40)
    up = p.data_dir / "excel_gpt_uploads" / "n_check"
    up.mkdir(parents=True)
    reply = up / "gpt_reply.txt"
    reply.write_text("done\n", encoding="utf-8")

    save_operator_result(
        p,
        "n_check",
        input_paths=[frame],
        output_paths=[xlsx, reply],
        reply_text="done",
    )
    patch_operator_config(
        p, "n_check", {"emitKinds": ["result", "inputs"], "role": "assist"}
    )
    got = files_from_source_node(p, "n_check")
    names = {p.name for p in got}
    assert "project.xlsx" in names
    assert "a.png" in names
    assert "gpt_reply.txt" not in names
