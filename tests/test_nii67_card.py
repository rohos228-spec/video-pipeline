"""Карточка NII 67: две двери, блоки, один ГГ, общий финал."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.nii67_card import (
    CANONICAL_KINDS,
    FINALE_LIMIT,
    TEXT_LIMIT,
    card_from_payload,
    compile_scenario,
    default_card,
    delete_block,
    duplicate_block,
    insert_block,
    move_block,
    normalize_card,
    reorder_block,
    update_block,
    update_door,
    update_finale,
    update_gg,
    update_intro,
)


def test_default_card_is_two_parallel_doors() -> None:
    card = default_card()
    assert [door["id"] for door in card["doors"]] == ["door1", "door2"]
    assert card["doors"][0]["title"] == "Door 1"
    assert card["doors"][1]["title"] == "Door 2"
    left = [block["kind"] for block in card["doors"][0]["blocks"]]
    right = [block["kind"] for block in card["doors"][1]["blocks"]]
    assert left == list(CANONICAL_KINDS)
    assert right == list(CANONICAL_KINDS)
    left_ids = {block["id"] for block in card["doors"][0]["blocks"]}
    right_ids = {block["id"] for block in card["doors"][1]["blocks"]}
    assert left_ids.isdisjoint(right_ids)
    assert card["gg"]["name"] == ""
    assert card["finale"]["standard"] == ""


def test_insert_duplicate_delete_reorder_stay_on_one_door() -> None:
    card = default_card()
    before_right = [block["id"] for block in card["doors"][1]["blocks"]]
    card = insert_block(card, "door1", 2, "custom")
    custom = card["doors"][0]["blocks"][2]
    assert custom["kind"] == "custom"
    assert [block["kind"] for block in card["doors"][0]["blocks"]][1:4] == [
        "enter_action",
        "custom",
        "world_character",
    ]
    card = update_block(
        card,
        "door1",
        custom["id"],
        {"text": "мост между мирами", "custom_title": "Мост", "collapsed": True},
    )
    card = duplicate_block(card, "door1", custom["id"])
    copied = card["doors"][0]["blocks"][3]
    assert copied["text"] == "мост между мирами"
    assert copied["custom_title"] == "Мост"
    assert copied["id"] != custom["id"]
    assert copied["collapsed"] is False
    card = delete_block(card, "door1", custom["id"])
    ids = [block["id"] for block in card["doors"][0]["blocks"]]
    assert custom["id"] not in ids
    assert copied["id"] in ids
    card = move_block(card, "door1", copied["id"], -1)
    assert card["doors"][0]["blocks"][1]["id"] == copied["id"]
    card = move_block(card, "door1", card["doors"][0]["blocks"][0]["id"], -1)
    assert card["doors"][0]["blocks"][0]["kind"] == "world_frame"
    top_id = card["doors"][0]["blocks"][0]["id"]
    card = reorder_block(card, "door1", 0, 2)
    assert card["doors"][0]["blocks"][2]["id"] == top_id
    assert [block["id"] for block in card["doors"][1]["blocks"]] == before_right


def test_normalize_repairs_shape_and_limits() -> None:
    raw = {
        "doors": [
            {
                "id": "door1",
                "theme": "  Лес  ",
                "blocks": [
                    {"id": "same", "kind": "nope", "text": "x" * (TEXT_LIMIT + 50)},
                    {"id": "same", "kind": "exit", "collapsed": "да", "exit_action": " закрылась "},
                    "мусор",
                ],
            },
            {"id": "door3", "theme": "лишняя"},
            {"id": "door1", "theme": "повтор"},
        ],
        "finale": {"standard": "y" * (FINALE_LIMIT + 80), "notes": 12},
        "gg": {"name": " Лея ", "note": "одна"},
        "intro": {"text": " вход "},
    }
    card = normalize_card(raw)
    assert [door["id"] for door in card["doors"]] == ["door1", "door2"]
    assert card["doors"][0]["theme"] == "Лес"
    assert card["doors"][0]["blocks"][0]["kind"] == "custom"
    assert len(card["doors"][0]["blocks"][0]["text"]) == TEXT_LIMIT
    assert card["doors"][0]["blocks"][0]["id"] != card["doors"][0]["blocks"][1]["id"]
    assert card["doors"][0]["blocks"][1]["collapsed"] is True
    assert card["doors"][0]["blocks"][1]["exit_action"] == "закрылась"
    assert len(card["doors"][1]["blocks"]) == len(CANONICAL_KINDS)
    assert len(card["finale"]["standard"]) == FINALE_LIMIT
    assert card["finale"]["notes"] == ""
    assert card["gg"]["name"] == "Лея"
    assert card["intro"]["text"] == "вход"
    assert card["doors"][1]["title"] == "Door 2"


def test_unknown_creature_is_cleared_and_known_enum_kept() -> None:
    card = default_card()
    block_id = card["doors"][0]["blocks"][2]["id"]
    assert card["doors"][0]["blocks"][2]["kind"] == "world_character"
    card = update_block(
        card,
        "door1",
        block_id,
        {
            "character": {
                "enabled": True,
                "name": "Страж",
                "creature_type": "dragon",
                "temperament": "mysterious",
                "position": "справа от двери",
            }
        },
    )
    character = card["doors"][0]["blocks"][2]["character"]
    assert character["enabled"] is True
    assert character["creature_type"] == ""
    assert character["temperament"] == "mysterious"
    assert character["name"] == "Страж"
    card = update_block(
        card,
        "door1",
        block_id,
        {"character": {"creature_type": "beast"}},
    )
    assert card["doors"][0]["blocks"][2]["character"]["creature_type"] == "beast"
    assert card["doors"][0]["blocks"][2]["character"]["temperament"] == "mysterious"


def test_compile_one_gg_optional_world_character_and_shared_finale() -> None:
    card = default_card()
    card = update_intro(card, "Герой стоит перед двумя дверями.")
    card = update_gg(card, name="Лея", note="одна на весь ролик")
    card = update_door(card, "door1", {"theme": "Фэнтези / Древний лес", "theme_note": "мох и туман"})
    card = update_door(card, "door2", {"theme": "Киберпанк / Заброшенная станция"})
    enter_id = next(
        block["id"] for block in card["doors"][0]["blocks"] if block["kind"] == "enter_action"
    )
    card = update_block(card, "door1", enter_id, {"text": "Шелест листьев."})
    char_id = next(
        block["id"] for block in card["doors"][0]["blocks"] if block["kind"] == "world_character"
    )
    gg_id = next(block["id"] for block in card["doors"][1]["blocks"] if block["kind"] == "gg_action")
    card = update_block(card, "door2", gg_id, {"text": "Лея смотрит на панель."})
    exit_id = next(block["id"] for block in card["doors"][0]["blocks"] if block["kind"] == "exit")
    card = update_block(
        card,
        "door1",
        exit_id,
        {
            "exit_action": "дверь закрывается",
            "exit_animation": "створки сходятся",
            "room_after": "тихая комната",
        },
    )
    card = update_finale(card, standard="Общий посыл.", notes="Без третьей двери.")
    text = compile_scenario(card)
    assert text.count("ГЛАВНЫЙ ГЕРОЙ") == 1
    assert "Лея" in text
    assert "одна на весь ролик" in text
    assert "Door 1" in text and "Door 2" in text
    assert "Фэнтези / Древний лес" in text
    assert "Киберпанк / Заброшенная станция" in text
    assert "Шелест листьев." in text
    assert "Персонаж мира не используется." in text
    assert "дверь закрывается" in text
    assert "тихая комната" in text
    assert "ОБЩИЙ ФИНАЛ" in text
    assert "Общий посыл." in text
    assert "Действие единственного главного героя (Лея)." in text

    card = update_block(
        card,
        "door1",
        char_id,
        {
            "character": {
                "enabled": True,
                "name": "Лесной страж",
                "creature_type": "spirit",
                "temperament": "calm",
                "position": "в тени",
                "behavior": "стоит и смотрит",
                "appearance": "дымка",
            }
        },
    )
    filled = compile_scenario(card)
    assert "Лесной страж" in filled
    assert "Тип: Дух" in filled
    assert "Характер: Спокойный" in filled
    assert filled.count("ГЛАВНЫЙ ГЕРОЙ") == 1
    assert "Персонаж мира не используется." in filled


def test_empty_blocks_list_is_kept_and_missing_door_is_restored() -> None:
    card = normalize_card({"doors": [{"id": "door2", "blocks": []}]})
    assert card["doors"][0]["id"] == "door1"
    assert len(card["doors"][0]["blocks"]) == len(CANONICAL_KINDS)
    assert card["doors"][1]["blocks"] == []


def test_payload_requires_a_card_object() -> None:
    with pytest.raises(ValueError):
        card_from_payload({})
    with pytest.raises(ValueError):
        card_from_payload({"card": "нет"})
    card = card_from_payload({"card": {"intro": {"text": "раз"}, "gg": {"name": "Нил"}}})
    assert card["intro"]["text"] == "раз"
    assert card["gg"]["name"] == "Нил"
    assert len(card["doors"]) == 2


def test_prompt_file_describes_parallel_doors_and_one_gg() -> None:
    text = Path("prompts/02_script/nii67.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "двер" in lowered
    assert "один" in lowered
    assert "финал" in lowered
