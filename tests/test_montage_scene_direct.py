"""Прямой «Улучшить»: кадры из промта, не из кусков закадра."""

from __future__ import annotations

from app.services.montage_scene_direct import (
    attach_shot_coverage,
    build_direct_improve_prompt,
    build_kadry,
    infer_light,
    parse_direct_reply,
)
from app.services.montage_scene_improve import SHOT_ACTION_BODY_RULES
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


def test_short_vo_keeps_all_prompt_shots() -> None:
    vo = "Тед Банди Когда люди вспоминают Теда Банди,"
    assert len(split_scene_vo(vo)) == 1
    kadry = build_kadry(
        vo=vo,
        place="архив",
        parent_plan="ОБЩИЙ",
        parent_action="место архив, персонажи лицом к камере",
        shots=[
            {"действие": "идёт по архиву", "план": "СРЕДНИЙ"},
            {"действие": "хватает газету", "план": "КРУПНЫЙ"},
            {"действие": "смотрит на заголовок", "план": "ДЕТАЛЬ"},
        ],
        cell_number=1,
        operator_prompt="идёт по архиву → хватает газету → смотрит на заголовок",
    )
    acts = [str(s.get("действие") or "") for s in kadry]
    assert acts == [
        "идёт по архиву",
        "хватает газету",
        "смотрит на заголовок",
    ]
    assert "лицом к камере" not in " ".join(acts)
    assert " ".join(str(s.get("закадр") or "") for s in kadry).split() == vo.split()


def test_operator_arrows_win_when_gpt_returns_only_establishing() -> None:
    vo = "короткий закадр одной фразой про архив"
    kadry = build_kadry(
        vo=vo,
        place="архив",
        parent_plan="ОБЩИЙ",
        parent_action="место архив, персонажи лицом к камере",
        shots=[
            {"действие": "Показать общий вид архива газет, без людей в кадре.", "план": "ОБЩИЙ"},
        ],
        cell_number=2,
        operator_prompt="идёт к полке → берёт газету → читает заголовок",
    )
    acts = [str(s.get("действие") or "") for s in kadry]
    assert acts == ["идёт к полке", "берёт газету", "читает заголовок"]
    assert "лицом к камере" not in " ".join(acts)


def test_forest_improve_does_not_clone_the_attack() -> None:
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
    assert PARENT not in acts
    assert "лицом к камере" not in " ".join(acts)
    assert acts[0] == PLACE
    assert acts.count(ATTACK) == 1
    assert len(acts) == 2
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
    assert acts == ["кладёт носок"]
    assert " ".join(str(s.get("закадр") or "") for s in kadry).split() == vo.split()
    assert kadry[0]["план"] == "ДЕТАЛЬ"
    assert kadry[0]["ракурс"]
    assert kadry[0]["движение"]
    assert kadry[0]["стык"]
    assert kadry[0]["роль"]
    assert kadry[0]["объект"]


def test_parse_direct_reply_keeps_scene_card_and_shot_coverage() -> None:
    parsed = parse_direct_reply(
        """
        {
          "сцена": "Ночной архив, Банди у полки",
          "место": "газетный архив",
          "персонажи": "c01",
          "свет": "ночной",
          "предметы": "стопка газет",
          "фон": "стеллажи с папками",
          "смысл": "он обычный среди бумаг",
          "акцент": "заголовок на полосе",
          "особенность": "холодный верхний луч",
          "кадры": [
            {
              "действие": "идёт к полке",
              "план": "СРЕДНИЙ",
              "ракурс": "3/4",
              "движение": "следование",
              "стык": "cut",
              "роль": "вход",
              "объект": "тело"
            }
          ]
        }
        """
    )
    assert parsed["место"] == "газетный архив"
    assert parsed["свет"] == "ночной"
    assert parsed["предметы"] == "стопка газет"
    assert parsed["фон"] == "стеллажи с папками"
    assert parsed["кадры"][0]["ракурс"] == "3/4"
    assert parsed["кадры"][0]["движение"] == "следование"


def test_infer_light_from_text() -> None:
    assert infer_light("закат на дороге") == "закат"


def test_direct_improve_prompt_asks_full_shot_action() -> None:
    text = build_direct_improve_prompt(
        operator_prompt="ткач у двери",
        vo="Он подошёл к дому.",
        place="",
        registry_labels="c01 · Сергей Ткач",
        matched_labels="c01 · Сергей Ткач",
    )
    assert SHOT_ACTION_BODY_RULES in text
    assert "кто слева" in text
    assert "короткий глагол" in text
    assert "одно видимое действие на кадр" not in text
    assert "Не пиши место/фон/свет" not in text


def test_attach_shot_coverage_never_leaves_empty_chips() -> None:
    kadry = attach_shot_coverage(
        [{"действие": "кладёт носок", "план": "", "закадр": "он положил носок"}],
        place="дом",
        cell_number=3,
    )
    shot = kadry[0]
    assert shot["план"]
    assert shot["ракурс"]
    assert shot["движение"]
    assert shot["стык"]
    assert shot["роль"]
    assert shot["объект"]
