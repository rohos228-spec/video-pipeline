"""Неактивные шоты не входят в проверки грамматики и не выкидываются из списка."""

from __future__ import annotations

import copy

from app.orchestrator.validators.grammar import (
    local_ensure_wide_before_dialogue,
    local_merge_trivial_shots,
    shots_of,
)
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


def test_merge_keeps_inactive_shot_and_its_voiceover() -> None:
    """Неактивный сосед не склеивается и не отдаёт закадр. Живая пара склеивается."""

    stolen = _shot(id="b", действие="сидит дальше", закадр="секрет")
    stolen["активен"] = False
    stolen["порядок"] = 7
    frozen_stolen = copy.deepcopy(stolen)
    pair = [_shot(id="a"), stolen]
    notes = local_merge_trivial_shots(pair)
    assert notes == []
    assert [shot["id"] for shot in pair] == ["a", "b"]
    assert stolen == frozen_stolen
    assert pair[0].get("закадр", "") == ""

    dead = _shot(id="dead", действие="смотрит в окно", кто="c02", объект="окно", место="улица")
    dead["активен"] = False
    dead["порядок"] = 9
    dead["закадр"] = "хвост"
    frozen_dead = copy.deepcopy(dead)
    shots = [_shot(id="a"), _shot(id="b", действие="сидит дальше"), dead]
    notes = local_merge_trivial_shots(shots)
    assert any("b склеен в a" in note for note in notes)
    assert [shot["id"] for shot in shots] == ["a", "dead"]
    assert dead == frozen_dead
    assert shots[0].get("закадр", "") == ""


def test_wide_ignores_inactive_speaker_and_keeps_the_shot() -> None:
    dead = _shot(id="s-K9", реплика="два", говорит="Борис", кто="c02", план="КРУПНЫЙ")
    dead["активен"] = False
    dead["порядок"] = 4
    frozen = copy.deepcopy(dead)
    shots = [
        _shot(id="s-K1", план="КРУПНЫЙ", действие="молчит"),
        _shot(id="s-K2", реплика="один", говорит="Анна", кто="c01"),
        dead,
    ]
    notes = local_ensure_wide_before_dialogue(shots)
    assert notes == []
    assert dead == frozen
    assert [shot["id"] for shot in shots] == ["s-K1", "s-K2", "s-K9"]
