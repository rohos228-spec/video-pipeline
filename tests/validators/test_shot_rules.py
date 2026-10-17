"""wide_before_dialogue и shot_merge совпадают с предикатами грамматики кадров."""

from __future__ import annotations

import copy

import pytest

from app.orchestrator.validators import VALIDATOR_CONTEXT_DRY_RUN, run
from app.orchestrator.validators.grammar import (
    local_ensure_wide_before_dialogue,
    local_merge_trivial_shots,
)


def _shot(**kwargs: str) -> dict[str, str]:
    base = {
        "id": "s-K1",
        "план": "СРЕДНИЙ",
        "ракурс": "фронт",
        "кто": "c01",
        "действие": "сидит",
        "место": "кухня",
        "объект": "стол",
        "реплика": "",
        "говорит": "",
    }
    base.update(kwargs)
    return base


def _dialogue_without_wide() -> list[dict[str, str]]:
    return [
        _shot(id="s-K1", план="КРУПНЫЙ", действие="молчит"),
        _shot(id="s-K2", реплика="привет", говорит="Анна", кто="c01"),
        _shot(id="s-K3", реплика="здравствуй", говорит="Борис", кто="c02"),
    ]


def _dialogue_with_wide() -> list[dict[str, str]]:
    shots = _dialogue_without_wide()
    shots[0]["план"] = "ОБЩИЙ"
    return shots


def _trivial_pair() -> list[dict[str, str]]:
    return [
        _shot(id="a"),
        _shot(id="b", действие="сидит дальше"),
    ]


def test_wide_missing_before_two_speakers_blocks() -> None:
    shots = _dialogue_without_wide()
    original = copy.deepcopy(shots)
    result = run("wide_before_dialogue", 1, {"shots": shots, VALIDATOR_CONTEXT_DRY_RUN: True})
    assert result.passed is False
    assert result.blocking is True
    assert result.details["violations"]
    assert shots == original


def test_wide_present_passes() -> None:
    result = run("wide_before_dialogue", 1, {"shots": _dialogue_with_wide()})
    assert result.passed is True
    assert result.details["violations"] == []


def test_single_speaker_does_not_require_wide() -> None:
    shots = [
        _shot(id="s-K1", план="КРУПНЫЙ"),
        _shot(id="s-K2", реплика="один", говорит="Анна"),
    ]
    result = run("wide_before_dialogue", 1, {"shots": shots})
    assert result.passed is True


def test_trivial_neighbors_block_shot_merge() -> None:
    shots = _trivial_pair()
    original = copy.deepcopy(shots)
    result = run("shot_merge", 1, {"shots": shots})
    assert result.passed is False
    assert result.blocking is True
    assert "b склеен в a" in result.details["violations"]
    assert shots == original


def test_reply_breaks_merge() -> None:
    shots = _trivial_pair()
    shots[1]["реплика"] = "нет"
    shots[1]["говорит"] = "Анна"
    result = run("shot_merge", 1, {"frames": shots})
    assert result.passed is True


def test_no_shots_is_not_a_failure() -> None:
    assert run("wide_before_dialogue", 1, {}).passed is True
    assert run("shot_merge", 1, {}).details["reason"] == "no_shots"


def test_shots_taken_from_after_snapshot() -> None:
    result = run("shot_merge", 1, {"after": {"frames": _trivial_pair()}})
    assert result.passed is False


def test_local_predicates_match_r0a_when_module_exists() -> None:
    """TODO(R0a): пока scene_shot_grammar не в main, сравнение пропускается."""

    try:
        from app.services import scene_shot_grammar as grammar
    except ImportError:
        pytest.skip("ждёт merge R0a: app.services.scene_shot_grammar")
    if not hasattr(grammar, "merge_trivial_shots") or not hasattr(grammar, "ensure_wide_before_dialogue"):
        pytest.skip("ждёт merge R0a: предикаты merge_trivial_shots / ensure_wide_before_dialogue")
    fixtures = [
        _dialogue_without_wide(),
        _dialogue_with_wide(),
        _trivial_pair(),
        [_shot(id="only")],
    ]
    for shots in fixtures:
        left = copy.deepcopy(shots)
        right = copy.deepcopy(shots)
        assert local_merge_trivial_shots(left) == grammar.merge_trivial_shots(right)
        assert left == right
        left = copy.deepcopy(shots)
        right = copy.deepcopy(shots)
        assert local_ensure_wide_before_dialogue(left) == grammar.ensure_wide_before_dialogue(right)
        assert left == right
