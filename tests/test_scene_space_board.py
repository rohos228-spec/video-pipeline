"""Карта площадки: гвозди родителя, дельта у детей."""

from __future__ import annotations

from app.services.scene_space_board import (
    apply_delta,
    compile_child_action,
    compile_parent_prompt,
    default_board,
    extract_nails,
    parse_board,
)


def test_extract_nails_from_forest_vo() -> None:
    nails = extract_nails(
        "ЛЕСОПОЛОСА",
        "Ткач выходит в лесополосу вдоль дороги. Девушка у тропы у края поля.",
    )
    folded = {n.casefold() for n in nails}
    assert "лесополоса" in folded
    assert "дорога" in folded
    assert "тропа" in folded
    assert 3 <= len(nails) <= 5


def test_parent_prompt_lists_nails_and_faces() -> None:
    board = default_board(
        place="ЛЕСОПОЛОСА",
        characters=[{"code": "c01", "name": "Сергей Ткач"}],
        vo="дорога тропа дерево",
        plan="ОБЩИЙ",
    )
    text = compile_parent_prompt(board)
    assert "Гвозди площадки" in text
    assert "лицом к камере" in text
    assert "не двигать" in text.casefold() or "Не переставлять" in text


def test_delta_does_not_rewrite_nails() -> None:
    parent = default_board(
        place="лес",
        characters=[{"code": "c01", "name": "Ткач"}],
        vo="ствол тропа дорога",
        plan="ОБЩИЙ",
    )
    nails = list(parent["гвозди"])
    child = apply_delta(parent, "CAM @тропа →ствол; SIZE СРЕДНИЙ")
    assert child["гвозди"] == nails
    assert child["камера"]["план"] == "СРЕДНИЙ"
    assert child["камера"]["где"]
    assert child["камера"]["где"] != parent["камера"]["где"] or child["камера"][
        "смотрит"
    ] != parent["камера"]["смотрит"]


def test_move_steps_person_keeps_set() -> None:
    parent = default_board(
        place="лес",
        characters=[{"code": "c01", "name": "Ткач"}],
        vo="ствол тропа дорога",
        plan="ОБЩИЙ",
    )
    here = parent["стоит"][0]["где"]
    dest = next(n for n in parent["гвозди"] if n.casefold() != here.casefold())
    child = apply_delta(parent, f"MOVE c01 @{dest}")
    assert child["гвозди"] == parent["гвозди"]
    assert child["стоит"][0]["где"] == dest
    line = compile_child_action(
        parent, child, delta=f"MOVE c01 @{dest}", beat="подходит ближе"
    )
    assert "та же площадка" in line
    assert "шагнул" in line


def test_parse_board_keeps_fallback_nails_if_gpt_sends_two() -> None:
    fallback = default_board(
        place="дом",
        characters=[{"code": "c01", "name": "Ткач"}],
        vo="дверь стол окно",
        plan="ОБЩИЙ",
    )
    parsed = parse_board({"гвозди": ["стол", "окно"]}, fallback=fallback)
    assert parsed["гвозди"] == fallback["гвозди"]
