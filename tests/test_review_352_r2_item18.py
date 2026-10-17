"""Повторное ревью #352 №18: порог существ не выше двух дверей варианта."""

from __future__ import annotations

from app.services.xlsx_step_runners import _min_creature_doors


def test_one_variant_floor_accepts_two_creature_doors() -> None:
    floor = _min_creature_doors(1)
    assert floor == 2
    creature_doors_ok = 0
    this_creature = 2
    assert creature_doors_ok + this_creature >= floor
    assert _min_creature_doors(5) == 3
