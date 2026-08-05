"""scenes → Frame.attrs: только scene-level, без дублей shot на все кадры."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.db_apply import (
    expand_scene_registry_onto_frames,
    strip_duplicated_shot_details,
)


def test_expand_fills_scene_place_but_not_shot_action() -> None:
    frames = [
        SimpleNamespace(
            attrs={"shot01_id_scene": "scene_01", "shot01_id_shot": "shot_01"}
        ),
        SimpleNamespace(
            attrs={"shot01_id_scene": "scene_01", "shot01_id_shot": "shot_01"}
        ),
    ]
    registry = [
        {
            "id_scene": "scene_01",
            "start_words": "утром",
            "end_words": "вечером",
            "место": "метро Токио",
            "структура_сцены": "continuity",
            "shots": [
                {
                    "id_shot": "shot_01",
                    "действие": "НЕ копировать на все кадры",
                    "описание_кадра": "тоже не копировать",
                }
            ],
        }
    ]
    n = expand_scene_registry_onto_frames(frames, registry)  # type: ignore[arg-type]
    assert n == 2
    assert frames[0].attrs["place"] == "метро Токио"
    assert frames[0].attrs["scene_structure"] == "continuity"
    assert "shot01_action" not in frames[0].attrs
    assert "main_action" not in frames[0].attrs
    assert frames[1].attrs.get("shot01_action") in (None, "")


def test_strip_duplicated_shot_details() -> None:
    same = "пассажиры входят в метро"
    frames = [
        SimpleNamespace(attrs={"shot01_action": same, "place": "метро"}),
        SimpleNamespace(attrs={"shot01_action": same, "place": "метро"}),
        SimpleNamespace(attrs={"shot01_action": same, "place": "метро"}),
        SimpleNamespace(attrs={"shot01_action": "уникальный жест"}),
    ]
    n = strip_duplicated_shot_details(frames)  # type: ignore[arg-type]
    assert n == 3
    assert frames[0].attrs.get("shot01_action") in (None, "")
    assert frames[0].attrs["place"] == "метро"
    assert frames[3].attrs["shot01_action"] == "уникальный жест"
