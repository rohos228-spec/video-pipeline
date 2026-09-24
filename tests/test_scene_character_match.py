"""Сверка персонажей сцены с реестром Entity."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.montage_scene_direct import pick_scene_characters
from app.services.scene_character_match import (
    format_character_codes,
    intersect_codes_with_registry,
    match_registry_characters,
)


def _ents() -> list:
    return [
        SimpleNamespace(type="character", code="c01", name="Ткач Иван", attrs={}),
        SimpleNamespace(
            type="character",
            code="c02",
            name="Жена",
            attrs={"aliases": ["Мария"]},
        ),
        SimpleNamespace(type="character", code="c03", name="Приказчик", attrs={}),
        SimpleNamespace(type="prop", code="c99", name="станок", attrs={}),
    ]


def test_match_name_alias_and_skip_unknown_code() -> None:
    rows = match_registry_characters(
        "Ткач стоит у станка, Мария смотрит, код c99 мимо",
        _ents(),
    )
    assert [r["code"] for r in rows] == ["c01", "c02"]


def test_code_in_text_must_exist_in_registry() -> None:
    rows = match_registry_characters("в кадре c03 и c77", _ents())
    assert [r["code"] for r in rows] == ["c03"]


def test_unmentioned_name_stays_out() -> None:
    rows = match_registry_characters("цех гудит, станок рвёт кромку", _ents())
    assert rows == []


def test_gpt_cannot_add_unmentioned_registry_code() -> None:
    picked = pick_scene_characters(
        haystack="Ткач чинит челнок",
        gpt_raw="c01, c03",
        entities=_ents(),
    )
    assert format_character_codes(picked) == "c01"


def test_intersect_falls_back_to_names_when_no_codes() -> None:
    rows = intersect_codes_with_registry("Мария у двери", _ents())
    assert [r["code"] for r in rows] == ["c02"]
