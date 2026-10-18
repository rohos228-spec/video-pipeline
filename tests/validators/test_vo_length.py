"""Единый источник нормы длины закадра: 200–300 символов."""

from __future__ import annotations

import inspect

from app.orchestrator.validators.vo_length import (
    MAX_VO_CHARS,
    MIN_VO_CHARS,
    check_vo_lengths,
)
from app.services.scene_boundaries import fragment_stats


def test_bounds_are_200_300() -> None:
    assert MIN_VO_CHARS == 200
    assert MAX_VO_CHARS == 300


def test_check_vo_lengths_flags_short_and_long_only() -> None:
    fragments = ["x" * 199, "y" * 200, "z" * 300, "w" * 301]
    bad = check_vo_lengths(fragments)
    assert [(v.index, v.kind, v.length) for v in bad] == [
        (0, "too_short", 199),
        (3, "too_long", 301),
    ]
    assert check_vo_lengths(["а" * 250]) == []
    assert check_vo_lengths([]) == []


def test_scene_boundaries_uses_the_same_function() -> None:
    src = inspect.getsource(fragment_stats)
    assert "check_vo_lengths" in src
    assert "> 300" not in src
    assert "< 200" not in src

    fragments = ["a" * 100, "b" * 250, "c" * 400, "d" * 350]
    stats = fragment_stats(fragments)
    violations = check_vo_lengths(fragments)
    assert stats["over_300"] == sum(1 for v in violations if v.kind == "too_long")
    assert stats["under_200"] == sum(1 for v in violations if v.kind == "too_short")
    assert stats["over_300"] == 2
    assert stats["under_200"] == 1
