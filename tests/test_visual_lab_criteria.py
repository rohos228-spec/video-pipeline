"""Unit tests for the weighted scoring formula and criterion metadata."""

from __future__ import annotations

import pytest

from app.services.visual_lab.criteria import (
    CRITERIA,
    CRITERION_BY_ID,
    CRITERION_IDS,
    GROUP_TO_CRITERIA,
    GROUPS,
    weighted_score,
)


def test_exactly_20_criteria() -> None:
    assert len(CRITERIA) == 20
    assert len(CRITERION_IDS) == 20
    assert len(set(CRITERION_IDS)) == 20  # no dupes


def test_exactly_6_groups() -> None:
    assert {g.id for g in GROUPS} == {"A", "B", "C", "D", "E", "F"}


def test_every_criterion_is_in_exactly_one_group() -> None:
    seen: set[str] = set()
    for g in GROUPS:
        for cid in GROUP_TO_CRITERIA[g.id]:
            assert cid not in seen
            seen.add(cid)
    assert seen == set(CRITERION_IDS)


def test_group_weights_match_spec() -> None:
    weights = {g.id: g.weight for g in GROUPS}
    assert weights == {"A": 1.3, "B": 1.2, "C": 1.2, "D": 1.3, "E": 1.4, "F": 1.1}


def test_perfect_scores_yield_10() -> None:
    perfect = {c.id: 10.0 for c in CRITERIA}
    assert weighted_score(perfect) == pytest.approx(10.0)


def test_zero_minus_ish_scores_yield_one() -> None:
    floor_scores = {c.id: 1.0 for c in CRITERIA}
    assert weighted_score(floor_scores) == pytest.approx(1.0)


def test_weighted_score_punishes_low_groups_more_when_weighted() -> None:
    # Group E has weight 1.4 (max). Tanking E should drop the score more
    # than tanking F (weight 1.1).
    base = {c.id: 8.0 for c in CRITERIA}
    tank_e = dict(base)
    for cid in GROUP_TO_CRITERIA["E"]:
        tank_e[cid] = 2.0
    tank_f = dict(base)
    for cid in GROUP_TO_CRITERIA["F"]:
        tank_f[cid] = 2.0
    score_tank_e = weighted_score(tank_e)
    score_tank_f = weighted_score(tank_f)
    assert score_tank_e < score_tank_f


def test_weighted_score_empty_returns_zero() -> None:
    assert weighted_score({}) == 0.0


def test_partial_scores_still_compute() -> None:
    # Only group A scored; result should equal the group A average.
    partial = {cid: 7.0 for cid in GROUP_TO_CRITERIA["A"]}
    assert weighted_score(partial) == pytest.approx(7.0)


def test_lookup_by_id() -> None:
    for cid in CRITERION_IDS:
        c = CRITERION_BY_ID[cid]
        assert c.id == cid
        assert c.group in {g.id for g in GROUPS}
        assert c.name_ru
