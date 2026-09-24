"""Прямой «Улучшить»: кадры не клонируют одно действие."""

from __future__ import annotations

from app.services.montage_scene_direct import build_kadry
from app.services.scene_vo_split import split_scene_vo

PARENT = "место ЛЕСОПОЛОСА, c01 · Сергей Ткач лицом к камере"
PLACE = "Лесополоса; Сергей Ткач в кадре."
ATTACK = (
    "Сергей Ткач нападает на девушку за границей кадра и начинает душить её; "
    "девушка не видна."
)
FOREST_VO = (
    "Сергей Ткач выходит в лесополосу вдоль дороги и смотрит по сторонам как случайный прохожий. "
    "Он замечает девушку у края поля и медленно подходит ближе между деревьями. "
    "Затем он хватает её за плечо и начинает душить так что её не видно зрителю. "
    "После этого Ткач уходит вдоль железнодорожных путей чтобы собаки потеряли след. "
    "Следствие ещё не видело общей картины и искало случайного чужака вместо соседа. "
    "Место он выбрал не случайно: рядом дорога, лесополоса и железная дорога."
)


def test_forest_improve_does_not_clone_the_attack() -> None:
    pieces = split_scene_vo(FOREST_VO)
    assert len(pieces) >= 3
    kadry = build_kadry(
        vo=FOREST_VO,
        place="ЛЕСОПОЛОСА",
        parent_plan="ОБЩИЙ",
        parent_action=PARENT,
        shots=[
            {"действие": PLACE, "план": "СРЕДНИЙ"},
            {"действие": ATTACK, "план": "СРЕДНИЙ"},
        ]
        + [{"действие": ATTACK, "план": "СРЕДНИЙ"}] * 9,
        cell_number=1,
    )
    acts = [str(s.get("действие") or "") for s in kadry]
    assert acts[0] == PARENT
    assert PLACE not in acts
    assert acts.count(ATTACK) == 1
    assert len(acts) == len({a.casefold() for a in acts})
    assert " ".join(str(s.get("закадр") or "") for s in kadry).split() == FOREST_VO.split()


def test_long_vo_does_not_repeat_last_gpt_line() -> None:
    vo = " ".join(["слово"] * 40)
    kadry = build_kadry(
        vo=vo,
        place="дом",
        parent_plan="СРЕДНИЙ",
        parent_action="место дом, ткач лицом к камере",
        shots=[{"действие": "кладёт носок", "план": "ДЕТАЛЬ"}],
        cell_number=3,
    )
    acts = [str(s.get("действие") or "") for s in kadry]
    assert acts[0] == "место дом, ткач лицом к камере"
    assert acts.count("кладёт носок") <= 1
    assert len(acts) == len({a.casefold() for a in acts})
    assert " ".join(str(s.get("закадр") or "") for s in kadry).split() == vo.split()
