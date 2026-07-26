"""forwardPaths / inherit: какие файлы отдаёт проверочная нода дальше."""

from __future__ import annotations

from pathlib import Path

from app.models import Project, ProjectStatus
from app.services.gpt_operator import apply_check_reply, files_from_source_node


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
    assert not any(x.name == "analysis.json" for x in got)


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
