"""Неактивные шоты не входят в проверки грамматики и не выкидываются из списка."""

from __future__ import annotations

import copy

from app.orchestrator.validators.grammar import shots_of
from tests.validators.test_shot_rules import _shot


def test_shots_of_hides_inactive_and_leaves_context() -> None:
    dead = _shot(id="dead", закадр="секрет")
    dead["активен"] = False
    dead["порядок"] = 2
    frozen = copy.deepcopy(dead)
    context = {
        "shots": [
            _shot(id="a"),
            dead,
            _shot(id="c"),
        ]
    }
    got = shots_of(context)
    assert got is not None
    assert [shot["id"] for shot in got] == ["a", "c"]
    assert context["shots"][1] == frozen

    via_after = shots_of({"after": {"кадры": context["shots"]}})
    assert via_after is not None
    assert [shot["id"] for shot in via_after] == ["a", "c"]
