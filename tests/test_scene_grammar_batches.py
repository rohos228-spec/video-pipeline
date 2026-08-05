"""Merge outline + shot batches for scene_grammar."""

from __future__ import annotations

from pathlib import Path

from app.services.scene_grammar_batches import (
    clear_scene_grammar_checkpoint,
    load_scene_grammar_checkpoint,
    merge_scene_grammar_payloads,
    save_scene_grammar_checkpoint,
    _scenes_need_shots,
)


def test_merge_fills_shots_into_outline_scenes() -> None:
    outline = {
        "characters": [{"id": "c01", "имя": "X"}],
        "scenes": [
            {
                "id_scene": "scene_01",
                "start_words": "a",
                "end_words": "b",
                "место": "метро",
                "shots": [],
            },
            {
                "id_scene": "scene_02",
                "start_words": "c",
                "end_words": "d",
                "место": "храм",
                "shots": [],
            },
        ],
        "ops": [],
        "report": "outline",
    }
    shots_batch = {
        "characters": [],
        "scenes": [
            {
                "id_scene": "scene_01",
                "shots": [
                    {"id_shot": "shot_01", "действие": "вход"},
                    {"id_shot": "shot_02", "действие": "пакет"},
                ],
            }
        ],
        "ops": [],
        "report": "shots1",
    }
    shots_batch2 = {
        "scenes": [
            {
                "id_scene": "scene_02",
                "shots": [{"id_shot": "shot_01", "действие": "молитва"}],
            }
        ],
        "ops": [],
    }
    merged = merge_scene_grammar_payloads([outline, shots_batch, shots_batch2])
    assert len(merged["characters"]) == 1
    assert len(merged["scenes"]) == 2
    by = {s["id_scene"]: s for s in merged["scenes"]}
    assert len(by["scene_01"]["shots"]) == 2
    assert by["scene_01"]["место"] == "метро"
    assert by["scene_02"]["shots"][0]["действие"] == "молитва"


def test_checkpoint_roundtrip_and_need_shots(tmp_path: Path) -> None:
    outline = {
        "characters": [{"id": "c01"}],
        "scenes": [
            {"id_scene": "scene_01", "shots": []},
            {"id_scene": "scene_02", "shots": []},
        ],
        "ops": [],
    }
    batch1 = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "shots": [{"id_shot": "shot_01", "действие": "a"}],
            }
        ]
    }
    save_scene_grammar_checkpoint(tmp_path, [outline, batch1])
    loaded = load_scene_grammar_checkpoint(tmp_path)
    assert loaded is not None
    assert len(loaded) == 2
    merged = merge_scene_grammar_payloads(loaded)
    need = _scenes_need_shots(merged["scenes"])
    assert [s["id_scene"] for s in need] == ["scene_02"]
    clear_scene_grammar_checkpoint(tmp_path)
    assert load_scene_grammar_checkpoint(tmp_path) is None
