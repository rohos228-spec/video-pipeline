"""scenes → Frame.attrs: bind by words; 1 shot → 1 frame; no clone on all columns."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.db_apply import (
    expand_scene_registry_onto_frames,
    strip_duplicated_shot_details,
)


def test_expand_binds_by_words_and_maps_one_shot_per_frame() -> None:
    frames = [
        SimpleNamespace(voiceover_text="Утром метро заполнено людьми", attrs={}),
        SimpleNamespace(voiceover_text="пакет лежит у двери вагона", attrs={}),
        SimpleNamespace(voiceover_text="ноги проходят мимо пакета", attrs={}),
    ]
    registry = [
        {
            "id_scene": "scene_01",
            "start_words": "Утром метро",
            "end_words": "мимо пакета",
            "место": "метро Токио",
            "структура_сцены": "continuity",
            "смысл_сцены": "посев угрозы",
            "акцент": "НЕ клонировать на все кадры",
            "shots": [
                {
                    "id_shot": "shot_01",
                    "акцент": "поток у дверей",
                    "действие": "поток людей в вагоне",
                    "описание_кадра": "общий план вагона",
                    "особенность_сцены": "Общий план локации",
                },
                {
                    "id_shot": "shot_02",
                    "акцент": "пакет у порога",
                    "действие": "камера на пакете",
                    "описание_кадра": "insert пакета",
                    "особенность_сцены": "Деталь объекта",
                },
            ],
        }
    ]
    n = expand_scene_registry_onto_frames(frames, registry)  # type: ignore[arg-type]
    assert n == 3
    assert frames[0].attrs["shot01_id_scene"] == "scene_01"
    assert frames[0].attrs["place"] == "метро Токио"
    assert frames[0].attrs["shot01_action"] == "поток людей в вагоне"
    assert frames[0].attrs["accent"] == "поток у дверей"
    assert frames[1].attrs["accent"] == "пакет у порога"
    assert frames[1].attrs["shot01_action"] == "камера на пакете"
    # 3-й кадр в сцене, shots только 2 → scene-level без клона действия/акцента
    assert frames[2].attrs["place"] == "метро Токио"
    assert frames[2].attrs.get("shot01_action") in (None, "")
    assert frames[2].attrs.get("accent") in (None, "")


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
