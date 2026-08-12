"""Парсеры и валидаторы scene_design (без БД)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.scene_design import agents as ag
from app.services.scene_design.assembler import validate_payload


def _fence(payload: dict) -> str:
    return "текст\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\nхвост"


# ── extract_json_object ──────────────────────────────────────────────


def test_extract_json_from_fence() -> None:
    data = ag.extract_json_object(
        _fence({"characters": [{"id": "c01"}]}), marker_keys=("characters",)
    )
    assert data == {"characters": [{"id": "c01"}]}


def test_extract_json_bare_object() -> None:
    text = 'Вот результат: {"characters": [{"id": "c01"}], "report": "ok"} спасибо'
    data = ag.extract_json_object(text, marker_keys=("characters",))
    assert data is not None
    assert data["characters"][0]["id"] == "c01"


def test_extract_json_none_on_garbage() -> None:
    assert ag.extract_json_object("нет json вообще", marker_keys=("characters",)) is None
    assert ag.extract_json_object("", marker_keys=("characters",)) is None


# ── parse_agent_slice ────────────────────────────────────────────────


def test_parse_slice_ok() -> None:
    data = ag.parse_agent_slice(
        "characters", _fence({"characters": [{"id": "c01"}], "report": "r"})
    )
    assert data["characters"][0]["id"] == "c01"


def test_parse_slice_error_field() -> None:
    with pytest.raises(ag.SceneDesignAgentError, match="error"):
        ag.parse_agent_slice("world", _fence({"error": "не смог", "locations": []}))


def test_parse_slice_empty_list_rejected() -> None:
    with pytest.raises(ag.SceneDesignAgentError, match="пустой"):
        ag.parse_agent_slice("camera", _fence({"shot_plan": []}))


def test_parse_slice_no_json() -> None:
    with pytest.raises(ag.SceneDesignAgentError, match="нет JSON"):
        ag.parse_agent_slice("action", "просто текст без данных")


def test_loads_json_loose_strips_block_comments() -> None:
    raw = '{"scenes": [ /* stub */ {"id_scene": "scene_01"} ], "x": 1,}'
    data = ag.loads_json_loose(raw)
    assert data["scenes"][0]["id_scene"] == "scene_01"


def test_parse_skeleton_accepts_commented_stub_and_full() -> None:
    # Stub с пустым scenes + рабочий объект — берём непустой.
    text = (
        '```json\n{"scenes": [ /* карточки */ ], "map": {}}\n```\n'
        + _fence(
            {
                "scenes": [
                    {
                        "id_scene": "scene_01",
                        "кадры": [1],
                        "суть": "тест",
                    }
                ],
                "characters_seed": [],
            }
        )
    )
    # Первый fence с комментарием: loose парсит scenes как [] после strip?
    # Главное — непустой scenes из второго объекта.
    data = ag.parse_agent_slice("skeleton", text)
    assert data["scenes"][0]["id_scene"] == "scene_01"


def test_parse_skeleton_scenes_alias() -> None:
    data = ag.parse_agent_slice(
        "skeleton",
        _fence({"сцены": [{"id_scene": "scene_01", "кадры": [1], "суть": "x"}]}),
    )
    assert data["scenes"][0]["id_scene"] == "scene_01"


def test_parse_skeleton_scenes_after_nested_map_frames() -> None:
    """Регресс: rfind('{{') перед \"scenes\" брал кадр из map.кадры."""
    payload = {
        "map": {
            "кадры": [
                {
                    "number": 1,
                    "места": ["x"],
                    "время": [],
                    "персонажи": [],
                    "предметы": ["y"],
                },
                {
                    "number": 2,
                    "места": ["z"],
                    "время": [],
                    "персонажи": ["A"],
                    "предметы": [],
                },
            ]
        },
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1, 2],
                "суть": "ok",
                "нити": [],
            }
        ],
        "characters_seed": [],
        "locations_seed": [],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data = ag.parse_agent_slice("skeleton", raw)
    assert len(data["scenes"]) == 1
    assert data["scenes"][0]["id_scene"] == "scene_01"


# ── parse_assembler_payload ──────────────────────────────────────────


def _payload(scenes=None, ops=None, characters=None) -> dict:
    return {
        "characters": characters if characters is not None else [{"id": "c01"}],
        "scenes": scenes if scenes is not None else [{"id_scene": "sc01"}],
        "ops": ops if ops is not None else [{"frame_uuid": "u1", "fields": {"id_scene": "sc01"}}],
        "report": "ok",
    }


def test_parse_assembler_ok() -> None:
    data = ag.parse_assembler_payload(_fence(_payload()))
    assert data["scenes"][0]["id_scene"] == "sc01"


def test_parse_assembler_empty_scenes_rejected() -> None:
    with pytest.raises(ag.SceneDesignAgentError, match="scenes"):
        ag.parse_assembler_payload(_fence(_payload(scenes=[])))


def test_parse_assembler_empty_ops_rejected() -> None:
    with pytest.raises(ag.SceneDesignAgentError, match="ops"):
        ag.parse_assembler_payload(_fence(_payload(ops=[])))


def test_parse_assembler_error_field() -> None:
    with pytest.raises(ag.SceneDesignAgentError, match="error"):
        ag.parse_assembler_payload(_fence({"error": "конфликт срезов"}))


# ── validate_payload ─────────────────────────────────────────────────

_VO = "Альфа начало истории. Бета середина пути. Гамма финал рассказа."
_FRAMES = [
    SimpleNamespace(uuid=f"u{i}", duration_seconds=3.0, voiceover_text=f"кадр {i}")
    for i in range(1, 4)
]
_PROJECT = SimpleNamespace(id=1)


def _valid_payload() -> dict:
    return {
        "characters": [],
        "scenes": [
            {
                "id_scene": "sc01",
                "start_words": "Альфа начало",
                "end_words": "финал рассказа",
                "время_сек": 9.0,
            }
        ],
        "ops": [
            {"frame_uuid": fr.uuid, "fields": {"id_scene": "sc01", "место": "лес"}}
            for fr in _FRAMES
        ],
    }


def test_validate_ok() -> None:
    assert validate_payload(_PROJECT, _FRAMES, _valid_payload(), _VO) == []


def test_validate_scene_time_required() -> None:
    p = _valid_payload()
    del p["scenes"][0]["время_сек"]
    problems = validate_payload(_PROJECT, _FRAMES, p, _VO)
    assert any("время_сек" in x for x in problems)


def test_validate_scene_time_mismatch() -> None:
    p = _valid_payload()
    p["scenes"][0]["время_сек"] = 25.0  # кадры дают 9.0
    problems = validate_payload(_PROJECT, _FRAMES, p, _VO)
    assert any("не бьётся с суммой кадров" in x for x in problems)


def test_validate_scene_time_within_tolerance() -> None:
    p = _valid_payload()
    p["scenes"][0]["время_сек"] = 10.0  # diff 1.0 < tolerance max(1.5, 3.15)
    assert validate_payload(_PROJECT, _FRAMES, p, _VO) == []


def test_validate_quote_not_in_voiceover() -> None:
    p = _valid_payload()
    p["scenes"][0]["start_words"] = "нет таких слов"
    problems = validate_payload(_PROJECT, _FRAMES, p, _VO)
    assert any("start_words" in x for x in problems)


def test_validate_unknown_frame_uuid() -> None:
    p = _valid_payload()
    p["ops"][0]["frame_uuid"] = "unknown"
    problems = validate_payload(_PROJECT, _FRAMES, p, _VO)
    assert any("неизвестный frame_uuid" in x for x in problems)
    assert any("без привязки" in x for x in problems)


def test_validate_duplicate_frame_uuid() -> None:
    p = _valid_payload()
    p["ops"][1]["frame_uuid"] = "u1"
    problems = validate_payload(_PROJECT, _FRAMES, p, _VO)
    assert any("дубль frame_uuid" in x for x in problems)


def test_validate_missing_frame_coverage() -> None:
    p = _valid_payload()
    p["ops"] = p["ops"][:2]
    problems = validate_payload(_PROJECT, _FRAMES, p, _VO)
    assert any("без привязки" in x for x in problems)


def test_validate_unknown_scene_id_in_ops() -> None:
    p = _valid_payload()
    p["ops"][0]["fields"]["id_scene"] = "sc99"
    problems = validate_payload(_PROJECT, _FRAMES, p, _VO)
    assert any("sc99" in x for x in problems)
