"""scenes[].shots[] → Frame.attrs (подробная запись в Базу)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.db_apply import expand_scene_registry_onto_frames


def test_expand_fills_place_and_shot_action_from_scenes() -> None:
    fr = SimpleNamespace(
        attrs={
            "shot01_id_scene": "scene_01",
            "shot01_id_shot": "shot_01",
            "scene_structure": "continuity",
        }
    )
    registry = [
        {
            "id_scene": "scene_01",
            "start_words": "утром",
            "end_words": "вечером",
            "место": "метро Токио",
            "акцент": "пакеты",
            "смысл_сцены": "атака начинается",
            "структура_сцены": "continuity",
            "shots": [
                {
                    "id_shot": "shot_01",
                    "действие": "пассажиры входят",
                    "описание_кадра": "вагоны в час пик",
                    "фон": "интерьер метро",
                    "предметы": "пакеты",
                    "логика_перехода": "к деталям",
                    "роль": "establish",
                }
            ],
        }
    ]
    n = expand_scene_registry_onto_frames([fr], registry)  # type: ignore[arg-type]
    assert n == 1
    assert fr.attrs["place"] == "метро Токио"
    assert fr.attrs["accent"] == "пакеты"
    assert fr.attrs["scene_sense"] == "атака начинается"
    assert fr.attrs["shot01_action"] == "пассажиры входят"
    assert fr.attrs["shot01_description"] == "вагоны в час пик"
    assert fr.attrs["shot01_bg"] == "интерьер метро"
    assert fr.attrs["main_action"] == "пассажиры входят"
    # уже заполненное не затираем
    assert fr.attrs["scene_structure"] == "continuity"


def test_expand_does_not_overwrite_existing_place() -> None:
    fr = SimpleNamespace(
        attrs={
            "shot01_id_scene": "scene_01",
            "shot01_id_shot": "shot_01",
            "place": "уже есть",
        }
    )
    registry = [
        {
            "id_scene": "scene_01",
            "start_words": "a",
            "end_words": "b",
            "место": "новое место",
            "shots": [{"id_shot": "shot_01", "действие": "бег"}],
        }
    ]
    expand_scene_registry_onto_frames([fr], registry)  # type: ignore[arg-type]
    assert fr.attrs["place"] == "уже есть"
    assert fr.attrs["shot01_action"] == "бег"
