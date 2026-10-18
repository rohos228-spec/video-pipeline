"""Неактивные шоты не входят в проверки грамматики и не выкидываются из списка."""

from __future__ import annotations

import builtins
import copy

import pytest

from app.orchestrator.validators import run
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


def test_run_keeps_inactive_shot_and_no_deletions_stays_quiet() -> None:
    dead = _shot(id="dead", действие="сидит дальше", закадр="секрет")
    dead["активен"] = False
    dead["порядок"] = 5
    frozen = copy.deepcopy(dead)
    shots = [_shot(id="a"), dead]
    frames = [{"id": "f1"}, {"id": "dead"}]
    context = {
        "shots": shots,
        "before": {"scenes": [{"id": "sc1"}], "frames": frames},
        "after": {"scenes": [{"id": "sc1"}], "frames": list(frames)},
    }
    merge = run("shot_merge", 1, context)
    wide = run("wide_before_dialogue", 1, context)
    deletions = run("no_deletions", 1, context)
    assert merge.passed is True
    assert merge.blocking is False
    assert wide.passed is True
    assert deletions.passed is True
    assert deletions.blocking is False
    assert context["shots"][1] == frozen

    removed = run(
        "no_deletions",
        1,
        {
            "before": {"frames": [{"id": "f1"}, {"id": "dead"}]},
            "after": {"frames": [{"id": "f1"}]},
        },
    )
    assert removed.passed is False
    assert removed.blocking is True
    assert removed.details["frames"]["missing_ids"] == ["dead"]


def test_tombstone_lookup_does_not_swallow_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сломанный frame_tombstones не прячется: ловим только ImportError."""

    import app.orchestrator.validators.grammar as grammar

    real_import = builtins.__import__

    def raising(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if name == "app.services.frame_tombstones":
            raise RuntimeError("broken tombstones")
        return real_import(name, globals, locals, fromlist, level)

    grammar._SHOT_IS_ACTIVE = grammar._re
    monkeypatch.setattr(builtins, "__import__", raising)
    try:
        with pytest.raises(RuntimeError, match="broken tombstones"):
            grammar.shot_counts_as_active({"id": "a"})
    finally:
        grammar._SHOT_IS_ACTIVE = grammar._re

    def missing(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if name == "app.services.frame_tombstones":
            raise ImportError("no module")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing)
    grammar._SHOT_IS_ACTIVE = grammar._re
    try:
        assert grammar.shot_counts_as_active({"активен": False}) is False
        assert grammar.shot_counts_as_active({"id": "z"}) is True
    finally:
        grammar._SHOT_IS_ACTIVE = grammar._re


def test_xlsx_false_and_zero_are_inactive() -> None:
    """Из xlsx ``активен`` приходит строкой ``false``/``нет`` или числом 0."""

    import app.orchestrator.validators.grammar as grammar

    grammar._SHOT_IS_ACTIVE = None
    try:
        assert grammar.shot_counts_as_active({"активен": "false"}) is False
        assert grammar.shot_counts_as_active({"активен": "FALSE"}) is False
        assert grammar.shot_counts_as_active({"активен": 0}) is False
        assert grammar.shot_counts_as_active({"активен": "0"}) is False
        assert grammar.shot_counts_as_active({"активен": "нет"}) is False
        assert grammar.shot_counts_as_active({"активен": False}) is False
        assert grammar.shot_counts_as_active({"активен": True}) is True
        assert grammar.shot_counts_as_active({"id": "a"}) is True
    finally:
        grammar._SHOT_IS_ACTIVE = grammar._re


def test_merge_skips_order_held_by_middle_inactive() -> None:
    """Склейка живых соседей не ставит им порядок, который уже занят неактивным."""

    first = _shot(id="a", кто="c03", место="чердак", действие="стоит")
    first["порядок"] = 1
    dead = _shot(id="dead", действие="смотрит в окно", кто="c09", объект="окно", место="улица")
    dead["активен"] = False
    dead["порядок"] = 2
    dead["закадр"] = "хвост"
    frozen = copy.deepcopy(dead)
    left = _shot(id="c")
    left["порядок"] = 3
    right = _shot(id="d", действие="сидит дальше")
    right["порядок"] = 4
    shots = [first, dead, left, right]

    notes = local_merge_trivial_shots(shots)
    assert any("d склеен в c" in note for note in notes)
    assert dead == frozen
    assert dead in shots
    orders = [shot["порядок"] for shot in shots if isinstance(shot, dict)]
    ids = [shot["id"] for shot in shots if isinstance(shot, dict)]
    assert len(orders) == len(set(orders)), orders
    assert len(ids) == len(set(ids)), ids
    assert {shot["id"] for shot in shots} == {"a", "dead", "c"}


def test_wide_insert_skips_numbers_held_by_middle_inactive() -> None:
    """Неактивный шот в середине держит свои id и порядок: живые их не повторяют."""

    dead = _shot(id="s-K2", план="КРУПНЫЙ", действие="молчит")
    dead["активен"] = False
    dead["порядок"] = 2
    frozen = copy.deepcopy(dead)
    shots = [
        _shot(id="s-K1", план="КРУПНЫЙ", действие="молчит"),
        dead,
        _shot(id="s-K3", реплика="один", говорит="Анна", кто="c01"),
        _shot(id="s-K4", реплика="два", говорит="Борис", кто="c02"),
    ]
    shots[0]["порядок"] = 1
    shots[2]["порядок"] = 3
    shots[3]["порядок"] = 4

    notes = local_ensure_wide_before_dialogue(shots)
    assert notes
    assert dead == frozen
    assert dead in shots
    ids = [shot["id"] for shot in shots if isinstance(shot, dict)]
    orders = [shot["порядок"] for shot in shots if isinstance(shot, dict) and "порядок" in shot]
    assert len(ids) == len(set(ids)), ids
    assert len(orders) == len(set(orders)), orders


@pytest.mark.parametrize("order", [2, 2.0, "2", "  2 "])
def test_reserved_order_accepts_xlsx_numbers(order: object) -> None:
    """Порядок из xlsx резервируется и склейкой, и вставкой общего плана."""

    first = _shot(id="a", кто="c03", место="чердак", действие="стоит")
    first["порядок"] = 1
    dead = _shot(id="dead", действие="смотрит в окно", кто="c09", объект="окно", место="улица")
    dead["активен"] = False
    dead["порядок"] = order
    dead["закадр"] = "хвост"
    frozen = copy.deepcopy(dead)
    left = _shot(id="c")
    left["порядок"] = 3
    right = _shot(id="d", действие="сидит дальше")
    right["порядок"] = 4
    merged = [first, dead, left, right]

    notes = local_merge_trivial_shots(merged)
    assert any("d склеен в c" in note for note in notes)
    assert dead == frozen
    live_orders = [
        shot["порядок"]
        for shot in merged
        if isinstance(shot, dict) and shot.get("id") != "dead"
    ]
    assert 2 not in live_orders, live_orders

    wide_dead = _shot(id="mid", план="КРУПНЫЙ", действие="молчит")
    wide_dead["активен"] = False
    wide_dead["порядок"] = order
    wide_frozen = copy.deepcopy(wide_dead)
    wide = [
        _shot(id="p1", план="КРУПНЫЙ", действие="молчит"),
        wide_dead,
        _shot(id="p3", реплика="один", говорит="Анна", кто="c01"),
        _shot(id="p4", реплика="два", говорит="Борис", кто="c02"),
    ]
    wide[0]["порядок"] = 1
    wide[2]["порядок"] = 3
    wide[3]["порядок"] = 4
    wide_notes = local_ensure_wide_before_dialogue(wide)
    assert wide_notes
    assert wide_dead == wide_frozen
    wide_orders = [
        shot["порядок"]
        for shot in wide
        if isinstance(shot, dict) and shot.get("id") != "mid" and "порядок" in shot
    ]
    assert 2 not in wide_orders, wide_orders

