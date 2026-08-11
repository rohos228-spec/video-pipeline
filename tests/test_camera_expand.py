"""Разворот camera SET: дробление VO-диапазона на кадры, не склейка."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scene_design.assembler import force_scenes_from_chrono
from app.services.scene_design.camera_expand import (
    clamp_shots_to_duration,
    expand_shot_plan_rows,
    parse_krupnost_ladder,
    rebuild_scenes_from_camera,
    required_shots_for_beat,
    split_text_into_parts,
)


def test_parse_ladder():
    assert parse_krupnost_ladder("VLS→CU") == ["VLS", "CU"]
    assert parse_krupnost_ladder("LS→MS→Insert") == ["LS", "MS", "Insert"]
    assert parse_krupnost_ladder("MCU left↔MCU right→CU") == [
        "MCU left",
        "MCU right",
        "CU",
    ]


def test_required_shots_uses_ladder_and_set():
    set_counts = {"SET_01": (3, 4)}
    assert (
        required_shots_for_beat(
            {"крупность": "VLS→CU", "набор": "SET_01"}, set_counts
        )
        == 3
    )
    assert (
        required_shots_for_beat({"крупность": "MS", "набор": None}, set_counts) == 1
    )
    # Длинный VO без лестницы — всё равно минимум шотов.
    assert (
        required_shots_for_beat(
            {"крупность": "MS", "набор": None}, set_counts, duration_sec=5.0
        )
        == 2
    )
    assert (
        required_shots_for_beat(
            {"крупность": "MS", "набор": None}, set_counts, duration_sec=9.0
        )
        == 3
    )


def test_clamp_shots_to_duration():
    assert clamp_shots_to_duration(4, 10) == 4
    assert clamp_shots_to_duration(4, 5) == 2
    assert clamp_shots_to_duration(3, 3) == 1


def test_split_text_into_parts():
    parts = split_text_into_parts("один два три четыре пять шесть", 3)
    assert len(parts) == 3
    assert " ".join(parts).split() == [
        "один",
        "два",
        "три",
        "четыре",
        "пять",
        "шесть",
    ]


def test_expand_shot_plan_rows():
    shots = [
        {
            "цитата": "Альфа",
            "крупность": "VLS→CU",
            "движение": "dolly-in→static",
            "набор": "SET_01",
        }
    ]
    exp = expand_shot_plan_rows(shots, {"SET_01": (3, 4)})
    assert len(exp) == 3
    assert exp[0]["крупность"] == "VLS"
    assert exp[0]["shot_index"] == 1
    assert exp[0]["shots_in_beat"] == 3


def _fr(num: int, uuid: str, text: str, sec: float = 5.0, **attrs):
    return SimpleNamespace(
        number=num,
        uuid=uuid,
        voiceover_text=text,
        duration_seconds=sec,
        sort_key=float(num),
        attrs=attrs.get("attrs", {}),
    )


def test_rebuild_one_scene_per_vo_parent_after_subdivide():
    """После дроби: 2 VO-родителя → 2 сцены, внутри по несколько кадров."""
    vo = "Альфа один тут слово. Бета два тут слово. Гамма три тут слово. Дельта четыре тут слово."
    # Два родителя, каждый уже раздроблен на 2 шота.
    frames = [
        _fr(
            1,
            "a1",
            "Альфа один тут слово.",
            attrs={
                "camera_subdivide": {
                    "parent_uuid": "a1",
                    "shot_index": 1,
                    "shots_in_beat": 2,
                    "ladder": ["VLS", "CU"],
                    "набор": "SET_01",
                }
            },
        ),
        _fr(
            2,
            "a2",
            "Бета два тут слово.",
            attrs={
                "camera_subdivide": {
                    "parent_uuid": "a1",
                    "shot_index": 2,
                    "shots_in_beat": 2,
                    "ladder": ["VLS", "CU"],
                    "набор": "SET_01",
                }
            },
        ),
        _fr(
            3,
            "b1",
            "Гамма три тут слово.",
            attrs={
                "camera_subdivide": {
                    "parent_uuid": "b1",
                    "shot_index": 1,
                    "shots_in_beat": 2,
                    "ladder": ["LS", "CU"],
                    "набор": "SET_40",
                }
            },
        ),
        _fr(
            4,
            "b2",
            "Дельта четыре тут слово.",
            attrs={
                "camera_subdivide": {
                    "parent_uuid": "b1",
                    "shot_index": 2,
                    "shots_in_beat": 2,
                    "ladder": ["LS", "CU"],
                    "набор": "SET_40",
                }
            },
        ),
    ]
    assembly = {
        "characters": [],
        "locations": [],
        "style_arc": [],
        "scenes_chrono": [],
        "shot_plan_chrono": [
            {
                "цитата": "Альфа один",
                "крупность": "VLS→CU",
                "набор": "SET_01",
                "мотив": "open",
            },
            {
                "цитата": "Гамма три",
                "крупность": "LS→CU",
                "набор": "SET_40",
                "мотив": "detail",
            },
        ],
    }
    out = rebuild_scenes_from_camera(
        frames,
        assembly,
        vo,
        set_counts={"SET_01": (3, 4), "SET_40": (2, 2)},
    )
    scenes = out["scenes_chrono"]
    assert len(scenes) == 2, scenes
    assert scenes[0]["кадров"] == 2
    assert scenes[1]["кадров"] == 2
    assert out["camera_expand_report"]["scenes"] == 2


def test_rebuild_does_not_merge_neighbor_vo_into_one_scene():
    """Без subdivide attrs — каждый Frame остаётся своей сценой (не merge)."""
    vo = "Альфа один тут. Бета два тут. Гамма три тут."
    frames = [
        _fr(1, "a", "Альфа один тут."),
        _fr(2, "b", "Бета два тут."),
        _fr(3, "c", "Гамма три тут."),
    ]
    assembly = {
        "scenes_chrono": [],
        "shot_plan_chrono": [
            {"цитата": "Альфа один", "крупность": "VLS→MS→CU", "набор": "SET_01"},
            {"цитата": "Гамма три", "крупность": "Insert→ECU", "набор": "SET_10"},
        ],
    }
    out = rebuild_scenes_from_camera(
        frames,
        assembly,
        vo,
        set_counts={"SET_01": (3, 4), "SET_10": (2, 2)},
    )
    # Без дроби БД — сцен не меньше числа VO-кадров (не склеиваем в 1–2).
    assert len(out["scenes_chrono"]) >= 2
    assert out["camera_expand_report"]["scenes"] >= 2


def test_rebuild_chrono_dyn_groups_by_id_scene():
    """chrono_dyn: кадры с одним id_scene → одна многокадровая сцена."""
    vo = "Альфа один тут слово. Бета два тут слово. Гамма три тут слово. Дельта четыре тут слово."
    # Как после subdivide inserted=0: каждый кадр = vo_parent с shots_in_beat=1.
    frames = [
        _fr(
            1,
            "a",
            "Альфа один тут слово.",
            attrs={
                "camera_subdivide": {
                    "role": "vo_parent",
                    "parent_uuid": "a",
                    "shot_index": 1,
                    "shots_in_beat": 1,
                }
            },
        ),
        _fr(
            2,
            "b",
            "Бета два тут слово.",
            attrs={
                "camera_subdivide": {
                    "role": "vo_parent",
                    "parent_uuid": "b",
                    "shot_index": 1,
                    "shots_in_beat": 1,
                }
            },
        ),
        _fr(
            3,
            "c",
            "Гамма три тут слово.",
            attrs={
                "camera_subdivide": {
                    "role": "vo_parent",
                    "parent_uuid": "c",
                    "shot_index": 1,
                    "shots_in_beat": 1,
                }
            },
        ),
        _fr(
            4,
            "d",
            "Дельта четыре тут слово.",
            attrs={
                "camera_subdivide": {
                    "role": "vo_parent",
                    "parent_uuid": "d",
                    "shot_index": 1,
                    "shots_in_beat": 1,
                }
            },
        ),
    ]
    assembly = {
        "scenes_chrono": [],
        "shot_plan_chrono": [
            {
                "id_scene": "scene_01",
                "phase_index": 1,
                "цитата": "Альфа один",
                "крупность": "WS",
                "переход": "eyeline",
            },
            {
                "id_scene": "scene_01",
                "phase_index": 2,
                "цитата": "Бета два",
                "крупность": "MS",
                "переход": "cut_on_action",
            },
            {
                "id_scene": "scene_02",
                "phase_index": 1,
                "цитата": "Гамма три",
                "крупность": "CU",
                "переход": "match_cut",
            },
            {
                "id_scene": "scene_02",
                "phase_index": 2,
                "цитата": "Дельта четыре",
                "крупность": "Insert",
                "переход": "sound_bridge",
            },
        ],
    }
    out = rebuild_scenes_from_camera(
        frames,
        assembly,
        vo,
        set_counts={},
        chrono_dyn=True,
    )
    scenes = out["scenes_chrono"]
    assert len(scenes) == 2, scenes
    assert scenes[0]["id_scene"] == "scene_01"
    assert scenes[0]["кадров"] == 2
    assert scenes[1]["id_scene"] == "scene_02"
    assert scenes[1]["кадров"] == 2
    assert out["camera_expand_report"]["multi_frame_scenes"] == 2
    assert out["camera_expand_report"]["single_frame_scenes"] == 0


def test_stamp_actions_from_chain():
    from app.services.scene_design.assembler import stamp_actions_onto_ops

    assembly = {
        "scenes_chrono": [
            {
                "id_scene": "scene_01",
                "цепь_действия": ["вошёл", "сел", "взял перо"],
                "кадры": [
                    {"uuid": "a"},
                    {"uuid": "b"},
                    {"uuid": "c"},
                ],
            }
        ]
    }
    gpt = {
        "ops": [
            {
                "frame_uuid": "a",
                "fields": {"id_scene": "scene_01", "действие": "камера вводит"},
            },
            {
                "frame_uuid": "b",
                "fields": {"id_scene": "scene_01", "действие": "камера вводит"},
            },
            {
                "frame_uuid": "c",
                "fields": {
                    "id_scene": "scene_01",
                    "действие": {"текст": "object"},
                },
            },
        ]
    }
    out = stamp_actions_onto_ops(gpt, assembly)
    assert out["ops"][0]["fields"]["действие"] == "вошёл"
    assert out["ops"][1]["fields"]["действие"] == "сел"
    assert out["ops"][2]["fields"]["действие"] == "взял перо"


def test_force_scenes_from_chrono_overrides_gpt_quotes():
    assembly = {
        "scenes_chrono": [
            {
                "id_scene": "scene_01",
                "start_words": "Альфа один тут",
                "end_words": "Бета два тут",
                "время_сек": 10,
                "кадров": 2,
                "кадры": [
                    {"uuid": "a", "number": 1},
                    {"uuid": "b", "number": 2},
                ],
                "структура_сцены": "continuity",
            }
        ]
    }
    gpt = {
        "characters": [],
        "scenes": [
            {
                "id_scene": "scene_99",
                "start_words": "ВЫДУМАННАЯ ЦИТАТА XXX",
                "end_words": "ТОЖЕ ФЕЙК",
                "время_сек": 10,
            }
        ],
        "ops": [
            {"frame_uuid": "a", "fields": {"id_scene": "scene_99", "x": 1}},
            {"frame_uuid": "b", "fields": {"id_scene": "scene_99", "x": 2}},
        ],
        "report": "gpt",
    }
    out = force_scenes_from_chrono(gpt, assembly)
    assert out["scenes"][0]["id_scene"] == "scene_01"
    assert out["scenes"][0]["start_words"] == "Альфа один тут"
    assert out["ops"][0]["fields"]["id_scene"] == "scene_01"
    assert out["ops"][1]["fields"]["id_scene"] == "scene_01"
