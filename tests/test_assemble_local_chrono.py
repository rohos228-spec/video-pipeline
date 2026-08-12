"""Локальная сборка chrono_dyn без GPT."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scene_design import assembler as sa
from app.services.scene_design.agents import extract_json_object


def test_extract_json_prefers_nonempty_marker_lists():
    text = """
```json
{"characters": [], "scenes": [], "ops": []}
```
Реальный ответ:
{"characters": [{"id": "c01"}], "scenes": [{"id_scene": "scene_01"}], "ops": [{"frame_uuid": "u1", "fields": {"id_scene": "scene_01"}}]}
"""
    data = extract_json_object(
        text, marker_keys=("scenes", "ops", "characters", "error")
    )
    assert data is not None
    assert data["scenes"][0]["id_scene"] == "scene_01"
    assert data["ops"][0]["frame_uuid"] == "u1"


def test_build_local_assembler_payload_covers_frames():
    frames = [
        SimpleNamespace(uuid="u1", number=1, voiceover_text="Альфа один.", duration_seconds=5.0),
        SimpleNamespace(uuid="u2", number=2, voiceover_text="Бета два.", duration_seconds=5.0),
    ]
    assembly = {
        "characters": [{"id": "c01", "имя": "Иван"}],
        "scenes_chrono": [
            {
                "id_scene": "scene_01",
                "start_words": "Альфа один",
                "end_words": "Бета два",
                "время_сек": 10.0,
                "кадров": 2,
                "кадры": [
                    {"uuid": "u1", "number": 1, "время_сек": 5},
                    {"uuid": "u2", "number": 2, "время_сек": 5},
                ],
                "цепь_действия": ["встаёт", "смотрит"],
            }
        ],
        "shot_plan_chrono": [
            {
                "цитата": "Альфа один",
                "композиция": "Иван в дверном проёме",
                "крупность": "средний план",
                "движение": "push-in",
                "кто_в_кадре": "c01",
                "набор": "SET_01",
                "мотив": "тревога",
            },
            {
                "цитата": "Бета два",
                "композиция": "Иван смотрит в окно",
                "крупность": "крупный план",
                "движение": "static",
                "кто_в_кадре": "c01",
                "набор": "SET_01",
                "мотив": "пауза",
            },
        ],
    }
    payload = sa.build_local_assembler_payload(assembly, frames)
    assert len(payload["scenes"]) == 1
    assert len(payload["ops"]) == 2
    assert {op["frame_uuid"] for op in payload["ops"]} == {"u1", "u2"}
    assert payload["ops"][0]["fields"]["id_scene"] == "scene_01"
    assert "встаёт" in (payload["ops"][0]["fields"].get("действие") or "")
    assert "крупность" not in payload["ops"][0]["fields"]
    assert "push-in" in (payload["ops"][0]["fields"].get("описание_shot01") or "")


def test_local_assemble_fills_place_lighting_cnn_and_splits_fields():
    frames = [
        SimpleNamespace(uuid="u1", number=1, voiceover_text="Альфа один.", duration_seconds=5.0),
        SimpleNamespace(uuid="u2", number=2, voiceover_text="Бета два.", duration_seconds=5.0),
    ]
    assembly = {
        "characters": [{"id": "c01", "имя": "Иван Петров"}],
        "locations": [{"id": "loc01", "name": "подъезд"}],
        "style_arc": [
            {
                "сцены": ["scene_01"],
                "освещение": "fluorescent overhead сверху",
                "палитра": ["серо-зелёный", "бетонный серый"],
                "тип_сцены": "реализм",
            }
        ],
        "scenes_chrono": [
            {
                "id_scene": "scene_01",
                "location": "loc01",
                "start_words": "Альфа один",
                "end_words": "Бета два",
                "время_сек": 10.0,
                "кадров": 2,
                "смысл_сцены": "общий смысл сцены",
                "кадры": [
                    {"uuid": "u1", "number": 1, "время_сек": 5},
                    {"uuid": "u2", "number": 2, "время_сек": 5},
                ],
                "цепь_действия": ["встаёт у двери", "смотрит в щель"],
            }
        ],
        "shot_plan_chrono": [
            {
                "цитата": "Альфа один",
                "композиция": "Иван в дверном проёме",
                "крупность": "средний план",
                "движение": "push-in",
                "кто_в_кадре": "Иван",
                "набор": None,
                "мотив": "тревога у порога",
            },
            {
                "цитата": "Бета два",
                "композиция": "Иван смотрит в окно",
                "крупность": "крупный план",
                "движение": "static",
                "кто_в_кадре": "Иван",
                "набор": None,
                "мотив": "пауза перед решением",
            },
        ],
    }
    payload = sa.build_local_assembler_payload(assembly, frames)
    sc = payload["scenes"][0]
    assert "loc01" in (sc.get("место") or "")
    assert "fluorescent" in (sc.get("освещение") or "")
    op0 = payload["ops"][0]["fields"]
    assert "loc01" in (op0.get("место") or "")
    assert "c01" in (op0.get("персонажи") or "")
    assert op0.get("акцент") != op0.get("смысл_сцены")
    assert op0.get("акцент") != op0.get("особенность_сцены")
    assert op0.get("смысл_сцены") != op0.get("особенность_сцены")
    assert "c01" in (sc.get("персонажи_сцены") or "")
