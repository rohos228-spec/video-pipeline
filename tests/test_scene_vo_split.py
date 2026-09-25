"""Нарезка закадра: 13–90, цель 50–70, цифра = 4 символа."""

from __future__ import annotations

from app.services.scene_vo_split import VO_MAX, VO_MIN, pack_vo_to_n, split_scene_vo, vo_weight


def test_digit_weighs_four() -> None:
    assert vo_weight("1") == 4
    assert vo_weight("a") == 1
    assert vo_weight("10") == 8
    assert vo_weight("1980") == 16
    assert vo_weight("a1") == 5
    assert vo_weight("  12  ") == 8


def test_short_text_stays_one_piece() -> None:
    text = "Ткач чинит челнок."
    assert vo_weight(text) < VO_MAX
    assert split_scene_vo(text) == [text]


def test_split_glue_equals_original_and_respects_weight() -> None:
    text = " ".join(["слово"] * 40)
    parts = split_scene_vo(text)
    assert " ".join(parts).split() == text.split()
    assert all(vo_weight(p) <= VO_MAX for p in parts)
    assert all(vo_weight(p) >= VO_MIN or len(parts) == 1 for p in parts)
    assert len(parts) >= 2


def test_digits_hit_limit_sooner_than_letters() -> None:
    letters = "слово " * 12 + "хвост"
    digits = "В 1980 1981 1982 1983 1984 1985 1986 1987 ткач работал у станка каждый день."
    assert vo_weight(letters) < vo_weight(digits)
    assert vo_weight(digits) > VO_MAX
    parts = split_scene_vo(digits)
    assert len(parts) >= 2
    assert " ".join(parts).split() == digits.split()
    assert all(vo_weight(p) <= VO_MAX for p in parts)


def test_pack_vo_to_n_glues_and_respects_max() -> None:
    text = " ".join(["слово"] * 40)
    parts = pack_vo_to_n(text, 3)
    assert " ".join(parts).split() == text.split()
    assert len(parts) == 3
    assert all(vo_weight(p) <= VO_MAX for p in parts)
    short = "Ткач чинит челнок."
    assert pack_vo_to_n(short, 5) == [short]


def test_pack_vo_to_n_never_grows_past_n() -> None:
    text = " ".join(["слово"] * 40)
    parts = pack_vo_to_n(text, 2)
    assert len(parts) == 2
    assert " ".join(parts).split() == text.split()
