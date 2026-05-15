"""Tests for word-test prompt mutation + knowledge update aggregation."""

from __future__ import annotations

from app.services.visual_lab.knowledge_update import rebuild_word_effects
from app.services.visual_lab.models import IterDoc, IterPrompt, KnowledgeBase, WordTest
from app.services.visual_lab.word_tests import _remove_token_ci, make_test_prompt


def _iter(text: str) -> IterDoc:
    return IterDoc(iter=1, phase="ok", prompt=IterPrompt(text=text))


def test_add_word_appends() -> None:
    out = make_test_prompt(
        _iter("kittens in pixel art"),
        operation="ADD",
        word="crisp pixel edges",
    )
    assert "crisp pixel edges" in out
    assert out.endswith("crisp pixel edges")


def test_add_word_is_idempotent() -> None:
    base = _iter("kittens in pixel art, crisp pixel edges")
    out = make_test_prompt(base, operation="ADD", word="crisp pixel edges")
    assert out.count("crisp pixel edges") == 1


def test_remove_word_strips_token() -> None:
    out = make_test_prompt(
        _iter("kittens, crisp pixel edges, anthropomorphic cats"),
        operation="REMOVE",
        word="crisp pixel edges",
    )
    assert "crisp pixel edges" not in out
    assert "anthropomorphic cats" in out


def test_remove_word_case_insensitive() -> None:
    out = _remove_token_ci("Soft Light, fur, soft light", "soft light")
    assert "soft light" not in out.lower()


def test_replace_word_swaps() -> None:
    out = make_test_prompt(
        _iter("kittens, blurry pixels"),
        operation="REPLACE",
        word="crisp pixel edges",
        replacement_for="blurry pixels",
    )
    assert "blurry pixels" not in out
    assert "crisp pixel edges" in out


def test_rebuild_word_effects_classifies_positive() -> None:
    kb = KnowledgeBase()
    tests = [
        WordTest(
            id=1, word="crisp pixel edges", operation="ADD",
            base_iter=1, test_iter=2,
            delta_per_criterion={"pixel_sharpness": 2.0},
            weighted_delta=1.8,
        ),
        WordTest(
            id=2, word="crisp pixel edges", operation="ADD",
            base_iter=3, test_iter=4,
            delta_per_criterion={"pixel_sharpness": 1.4},
            weighted_delta=1.6,
        ),
    ]
    rebuild_word_effects(kb, tests)
    assert "crisp pixel edges" in kb.word_effects
    eff = kb.word_effects["crisp pixel edges"]
    assert eff.tested == 2
    assert eff.stability == "STABLE_POSITIVE"
    assert eff.avg_weighted_delta > 1.0


def test_rebuild_word_effects_classifies_negative() -> None:
    kb = KnowledgeBase()
    tests = [
        WordTest(
            id=1, word="bad word", operation="ADD",
            base_iter=1, test_iter=2,
            weighted_delta=-1.8,
        ),
        WordTest(
            id=2, word="bad word", operation="ADD",
            base_iter=3, test_iter=4,
            weighted_delta=-1.6,
        ),
    ]
    rebuild_word_effects(kb, tests)
    assert kb.word_effects["bad word"].stability == "STABLE_NEGATIVE"


def test_rebuild_word_effects_combo_synergy() -> None:
    kb = KnowledgeBase()
    kb.word_effects.clear()
    individual_tests = [
        WordTest(id=1, word="A", operation="ADD",
                 base_iter=1, test_iter=2, weighted_delta=0.5),
        WordTest(id=2, word="B", operation="ADD",
                 base_iter=3, test_iter=4, weighted_delta=0.5),
    ]
    combo_tests = [
        WordTest(id=3, words=["A", "B"], operation="COMBO",
                 base_iter=5, test_iter=6, weighted_delta=2.0),
    ]
    rebuild_word_effects(kb, individual_tests + combo_tests)
    assert any(v.synergy_verdict == "SYNERGY" for v in kb.combo_effects.values())


def test_visual_rules_populated_from_stable_positive() -> None:
    kb = KnowledgeBase()
    tests = [
        WordTest(
            id=1, word="good word", operation="ADD",
            base_iter=1, test_iter=2,
            delta_per_criterion={"pixel_sharpness": 2.0, "fur_quality": 1.5},
            weighted_delta=2.0,
        ),
        WordTest(
            id=2, word="good word", operation="ADD",
            base_iter=3, test_iter=4,
            delta_per_criterion={"pixel_sharpness": 2.0, "fur_quality": 1.0},
            weighted_delta=1.8,
        ),
    ]
    rebuild_word_effects(kb, tests)
    do_rules = [r.rule for r in kb.visual_rules["do"]]
    assert any("good word" in r for r in do_rules)
