"""Нарезка и склейка входа/выхода сборщика scene_design."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scene_design import assemble_chunks as ac


def _fr(num: int, uuid: str, text: str, sec: float = 5.0):
    return SimpleNamespace(
        number=num,
        uuid=uuid,
        voiceover_text=text,
        duration_seconds=sec,
    )


def test_split_frame_batches():
    frames = [_fr(i, f"u{i}", f"текст {i}") for i in range(1, 8)]
    batches = ac.split_frame_batches(frames, max_frames=3)
    assert len(batches) == 3
    assert [f.number for f in batches[0]] == [1, 2, 3]
    assert [f.number for f in batches[-1]] == [7]


def test_filter_assembly_keeps_overlapping_scenes_and_shots():
    vo = "Альфа один. Бета два. Гамма три. Дельта четыре."
    frames = [
        _fr(1, "a", "Альфа один."),
        _fr(2, "b", "Бета два."),
        _fr(3, "c", "Гамма три."),
        _fr(4, "d", "Дельта четыре."),
    ]
    assembly = {
        "characters": [{"id": "c01", "имя": "X"}],
        "locations": [{"id": "loc01"}],
        "style_arc": [{"scene_hint": "мрачно"}],
        "scenes_chrono": [
            {
                "id_scene": "scene_01",
                "start_words": "Альфа один",
                "end_words": "Бета два",
                "кадры": [
                    {"uuid": "a", "number": 1, "время_сек": 5},
                    {"uuid": "b", "number": 2, "время_сек": 5},
                ],
                "кадров": 2,
                "время_сек": 10,
            },
            {
                "id_scene": "scene_02",
                "start_words": "Гамма три",
                "end_words": "Дельта четыре",
                "кадры": [
                    {"uuid": "c", "number": 3, "время_сек": 5},
                    {"uuid": "d", "number": 4, "время_сек": 5},
                ],
                "кадров": 2,
                "время_сек": 10,
            },
        ],
        "shot_plan_chrono": [
            {"цитата": "Альфа один", "набор": "SET_01"},
            {"цитата": "Гамма три", "набор": "SET_08"},
        ],
    }
    chunk = ac.filter_assembly_input_for_frames(
        assembly, frames[:2], frames, vo, include_characters=True
    )
    assert len(chunk["characters"]) == 1
    assert len(chunk["scenes_chrono"]) == 1
    assert chunk["scenes_chrono"][0]["id_scene"] == "scene_01"
    assert [k["uuid"] for k in chunk["scenes_chrono"][0]["кадры"]] == ["a", "b"]
    assert len(chunk["shot_plan_chrono"]) == 1
    assert chunk["shot_plan_chrono"][0]["набор"] == "SET_01"

    chunk2 = ac.filter_assembly_input_for_frames(
        assembly, frames[2:], frames, vo, include_characters=False
    )
    assert chunk2["characters"] == []
    assert len(chunk2["scenes_chrono"]) == 1
    assert chunk2["scenes_chrono"][0]["id_scene"] == "scene_02"


def test_merge_remaps_scene_ids_and_dedups_ops():
    p1 = {
        "characters": [{"id": "c01"}],
        "scenes": [{"id_scene": "scene_01", "start_words": "a", "end_words": "b"}],
        "ops": [
            {"frame_uuid": "u1", "fields": {"id_scene": "scene_01"}},
            {"frame_uuid": "u2", "fields": {"id_scene": "scene_01"}},
        ],
        "report": "c1",
    }
    p2 = {
        "characters": [],
        "scenes": [{"id_scene": "scene_01", "start_words": "c", "end_words": "d"}],
        "ops": [
            {"frame_uuid": "u2", "fields": {"id_scene": "scene_01"}},  # дубль
            {"frame_uuid": "u3", "fields": {"id_scene": "scene_01"}},
        ],
        "report": "c2",
    }
    merged = ac.merge_assembler_payloads([p1, p2])
    assert merged["characters"] == [{"id": "c01"}]
    assert [s["id_scene"] for s in merged["scenes"]] == ["scene_01", "scene_02"]
    uuids = {op["frame_uuid"] for op in merged["ops"]}
    assert uuids == {"u1", "u2", "u3"}
    by_u = {op["frame_uuid"]: op["fields"]["id_scene"] for op in merged["ops"]}
    assert by_u["u1"] == "scene_01"
    assert by_u["u3"] == "scene_02"
