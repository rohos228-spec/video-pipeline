"""D1: персонажи из checkpoint, если staging-ячейки пустые."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services.scene_design.apply import characters_from_payload_or_checkpoint
from app.services.scene_design.camera_expand import _is_shot_child, already_subdivided


def _project(tmp: Path, *, meta: dict | None = None) -> SimpleNamespace:
    sd = tmp / "scene_design"
    sd.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(data_dir=tmp, meta=meta or {})


def test_characters_from_payload_wins(tmp_path: Path) -> None:
    p = _project(tmp_path)
    payload = {"characters": [{"id": "c01", "имя": "А"}]}
    got = characters_from_payload_or_checkpoint(p, payload)
    assert [c["id"] for c in got] == ["c01"]


def test_characters_from_checkpoint_file(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        meta={
            "scene_design": {
                "agents": {"characters": {"status": "done"}},
            }
        },
    )
    (tmp_path / "scene_design" / "characters.json").write_text(
        json.dumps(
            {
                "characters": [
                    {"id": "c01", "имя": "Александр"},
                    {"id": "c04", "имя": "Надежда"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    got = characters_from_payload_or_checkpoint(p, {"characters": []})
    assert [c["id"] for c in got] == ["c01", "c04"]


def test_characters_from_raw_file_if_meta_stale(tmp_path: Path) -> None:
    p = _project(tmp_path, meta={})
    (tmp_path / "scene_design" / "characters.json").write_text(
        json.dumps({"characters": [{"id": "c02", "имя": "Людмила"}]}),
        encoding="utf-8",
    )
    got = characters_from_payload_or_checkpoint(p, {})
    assert got[0]["id"] == "c02"


def test_shot_child_vs_vo_parent() -> None:
    parent = SimpleNamespace(
        attrs={"camera_subdivide": {"role": "vo_parent", "shot_index": 1}}
    )
    child = SimpleNamespace(
        attrs={"camera_subdivide": {"role": "shot", "shot_index": 2}}
    )
    assert _is_shot_child(parent) is False
    assert _is_shot_child(child) is True
    assert already_subdivided([parent, child]) is True
    assert already_subdivided([parent]) is False
