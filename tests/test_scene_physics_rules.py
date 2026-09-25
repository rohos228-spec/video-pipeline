"""Физика действия и порядок планов: порог, возврат к общему, картинка ребёнка."""

from __future__ import annotations

from app.services.montage_scene_improve import (
    IMPROVE_ACTION_RULES,
    IMPROVE_SHOTS_RULES,
    finish_shots,
    qc_reasons,
    repair_shots,
    smooth_plan_sequence,
    units_from_cards,
    wide_return_reason,
)
from app.services.scene_shot_grammar import expand_action_to_shots, is_threshold_step
from app.services.vo_shot_expand import with_parent_scene_lock

VO = (
    "Он бежал по пустой улице, не оглядываясь, "
    "рванул дверь своего дома и влетел внутрь, "
    "прошёл по тёмному коридору прямо к туалету."
)


def _shot(n: int, action: str, plan: str, angle: str, obj: str = "тело") -> dict:
    return {
        "id": f"1-S1-K{n}",
        "parent_id": None if n == 1 else "1-S1-K1",
        "порядок": n,
        "сцена": 1,
        "якорь_n": 1,
        "место": "",
        "действие": action,
        "объект": obj,
        "роль": "действие",
        "план": plan,
        "ракурс": angle,
        "движение": "статика",
        "стык": "cut",
        "закадр": "",
    }


def test_threshold_step_detects_door_and_burst_in() -> None:
    assert is_threshold_step("c01 открывает входную дверь")
    assert is_threshold_step("вбегает в прихожую")
    assert not is_threshold_step("идёт по коридору к туалету")


def test_node_chain_after_door_is_not_new_wide() -> None:
    action = (
        "1. улица — бежит по улице → подбегает к закрытой двери → открывает дверь\n"
        "(Он бежал по пустой улице, не оглядываясь, рванул дверь своего дома)\n"
        "2. дом — вбегает в прихожую → идёт по коридору к туалету\n"
        "(и влетел внутрь, прошёл по тёмному коридору прямо к туалету.)"
    )
    shots = expand_action_to_shots(action, cell_number=1)
    inside = [s for s in shots if s["место"] == "дом"]
    assert inside, shots
    assert inside[0]["план"] != "ОБЩИЙ"
    assert inside[0]["стык"] == "рез по жесту"


def test_node_chain_jump_to_new_place_still_opens_wide() -> None:
    action = (
        "1. улица — бежит по улице → останавливается у фонаря\n"
        "(Он бежал по пустой улице, не оглядываясь,)\n"
        "2. вокзал — входит в зал ожидания → садится на скамью\n"
        "(а утром уже сидел на вокзале.)"
    )
    shots = expand_action_to_shots(action, cell_number=1)
    station = [s for s in shots if s["место"] == "вокзал"]
    assert station[0]["план"] == "ОБЩИЙ"


def test_medium_wide_medium_is_smoothed() -> None:
    shots = [
        _shot(1, "c01 бежит по улице к дому", "ОБЩИЙ", "3/4"),
        _shot(2, "c01 хватает ручку закрытой двери и открывает дверь", "СРЕДНИЙ", "3/4"),
        _shot(3, "c01 входит в прихожую", "ОБЩИЙ", "фронт", obj="место"),
        _shot(4, "c01 идёт по коридору к туалету", "СРЕДНИЙ", "3/4"),
    ]
    assert wide_return_reason(shots[2], shots[1])
    fixed = smooth_plan_sequence(shots)
    assert fixed
    assert [s["план"] for s in shots] == ["ОБЩИЙ", "СРЕДНИЙ", "СРЕДНИЙ", "СРЕДНИЙ"]
    assert shots[2]["стык"] == "cut_on_action"
    for a, b in zip(shots, shots[1:], strict=False):
        assert (a["план"], a["ракурс"]) != (b["план"], b["ракурс"])


def test_return_to_wide_mid_action_without_door_is_qc_error() -> None:
    shots = [
        _shot(1, "c01 сидит за столом и листает папку", "СРЕДНИЙ", "3/4"),
        _shot(2, "c01 откладывает папку", "ОБЩИЙ", "фронт"),
    ]
    cards = [{"n": 1, "place": "", "action": "листает → откладывает", "vo": VO}]
    units = units_from_cards(cards)
    shots[0]["закадр"] = VO[:60]
    shots[1]["закадр"] = VO[60:].strip()
    hard, _soft = qc_reasons(shots, units)
    assert any("общему" in h for h in hard)
    repair_shots(shots, units)
    hard, _soft = qc_reasons(shots, units)
    assert not any("общему" in h for h in hard)


def test_dissolve_and_new_place_keep_wide() -> None:
    prev = _shot(1, "c01 закрывает папку", "КРУПНЫЙ", "фронт")
    later = _shot(2, "c01 спит на скамье", "ОБЩИЙ", "3/4")
    later["стык"] = "dissolve"
    assert wide_return_reason(later, prev) is None
    moved = _shot(2, "c01 стоит на перроне", "ОБЩИЙ", "3/4")
    prev["место"] = "кабинет"
    moved["место"] = "вокзал: перрон"
    assert wide_return_reason(moved, prev) is None
    assert finish_shots([prev, moved])[1]["план"] == "ОБЩИЙ"


def test_prompts_carry_physics_and_plan_order() -> None:
    assert "Дверь закрыта" in IMPROVE_ACTION_RULES
    assert "два кадра" in IMPROVE_ACTION_RULES
    assert "слева/справа" in IMPROVE_ACTION_RULES.casefold()
    assert "Назад к ОБЩЕМУ" in IMPROVE_SHOTS_RULES
    assert "cut_on_action" in IMPROVE_SHOTS_RULES


def test_parent_lock_allows_new_zone_and_progress() -> None:
    text = with_parent_scene_lock("c01 вбегает в прихожую", has_parent_ref=True)
    assert text.startswith("Image 1 is the previous coverage still")
    assert "another zone" in text
    assert "further along the path" in text
    assert "left and right swap" in text
