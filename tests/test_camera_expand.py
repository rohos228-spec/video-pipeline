"""Разворот camera лестниц/SET → сцены с несколькими Frame."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scene_design.camera_expand import (
    expand_shot_plan_rows,
    parse_krupnost_ladder,
    rebuild_scenes_from_camera,
    required_shots_for_beat,
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
    assert len(exp) == 3  # SET min 3
    assert exp[0]["крупность"] == "VLS"
    assert exp[0]["shot_index"] == 1
    assert exp[0]["shots_in_beat"] == 3


def _fr(num: int, uuid: str, text: str, sec: float = 5.0):
    return SimpleNamespace(
        number=num, uuid=uuid, voiceover_text=text, duration_seconds=sec
    )


def test_rebuild_merges_frames_when_ladder_needs_multi():
    vo = "Альфа один тут. Бета два тут. Гамма три тут. Дельта четыре тут."
    frames = [
        _fr(1, "a", "Альфа один тут."),
        _fr(2, "b", "Бета два тут."),
        _fr(3, "c", "Гамма три тут."),
        _fr(4, "d", "Дельта четыре тут."),
    ]
    assembly = {
        "characters": [],
        "locations": [],
        "style_arc": [],
        "scenes_chrono": [
            {
                "id_scene": "scene_01",
                "start_words": "Альфа один",
                "end_words": "Альфа один тут",
                "кадры": [{"uuid": "a", "number": 1, "время_сек": 5}],
                "кадров": 1,
            },
            {
                "id_scene": "scene_02",
                "start_words": "Бета два",
                "end_words": "Бета два тут",
                "кадры": [{"uuid": "b", "number": 2, "время_сек": 5}],
                "кадров": 1,
            },
        ],
        "shot_plan_chrono": [
            {
                "цитата": "Альфа один",
                "крупность": "VLS→MS→CU",
                "набор": "SET_01",
                "мотив": "open",
            },
            {
                "цитата": "Гамма три",
                "крупность": "Insert→ECU",
                "набор": "SET_10",
                "мотив": "detail",
            },
        ],
    }
    out = rebuild_scenes_from_camera(
        frames,
        assembly,
        vo,
        set_counts={"SET_01": (3, 4), "SET_10": (2, 2)},
    )
    scenes = out["scenes_chrono"]
    assert scenes, "scenes rebuilt"
    # Первая сцена должна забрать ≥2 Frame под лестницу 3 шота.
    assert scenes[0]["кадров"] >= 2
    assert scenes[0]["camera_shots_required"] >= 3
    assert len(out["shot_plan_chrono"]) >= 3
    assert out["camera_expand_report"]["multi_frame_scenes"] >= 1
