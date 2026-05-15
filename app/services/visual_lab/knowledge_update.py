"""Aggregate word_tests → ``KnowledgeBase.word_effects`` & ``combo_effects``.

Pure-function logic, easy to unit-test. Called by the runner after each
word test completes, so the on-disk knowledge_base.json stays current.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.services.visual_lab.models import (
    ComboEffect,
    KnowledgeBase,
    VisualRule,
    WordEffect,
    WordTest,
)

_STABLE_THRESHOLD = 1.5  # weighted_delta magnitude
_UNSTABLE_SPREAD = 3.0    # max-min of repeat_scores
_SYNERGY_BONUS = 0.3      # combo - sum_individual > this → SYNERGY


def _classify_stability(
    weighted_delta: float, repeat_scores: list[float]
) -> str:
    if repeat_scores and (max(repeat_scores) - min(repeat_scores)) > _UNSTABLE_SPREAD:
        return "UNSTABLE"
    if abs(weighted_delta) < 0.1:
        return "STABLE"
    if weighted_delta >= _STABLE_THRESHOLD:
        return "STABLE_POSITIVE"
    if weighted_delta <= -_STABLE_THRESHOLD:
        return "STABLE_NEGATIVE"
    return "STABLE" if repeat_scores else "UNTESTED"


def rebuild_word_effects(
    kb: KnowledgeBase, tests: Iterable[WordTest]
) -> KnowledgeBase:
    """Recompute ``kb.word_effects`` and ``kb.combo_effects`` from all tests.

    Mutates and returns ``kb`` for convenience.
    """
    word_acc: dict[str, list[WordTest]] = {}
    combo_acc: dict[tuple[str, ...], list[WordTest]] = {}
    for t in tests:
        if t.operation == "COMBO" and t.words:
            key = tuple(sorted(t.words))
            combo_acc.setdefault(key, []).append(t)
        elif t.word:
            word_acc.setdefault(t.word, []).append(t)

    # Word effects.
    new_word_effects: dict[str, WordEffect] = {}
    for word, group in word_acc.items():
        deltas: dict[str, list[float]] = {}
        weighted_deltas: list[float] = []
        repeats: list[float] = []
        for t in group:
            for cid, dv in t.delta_per_criterion.items():
                deltas.setdefault(cid, []).append(float(dv))
            weighted_deltas.append(t.weighted_delta)
            repeats.extend(t.repeat_scores)
        avg_delta = {
            cid: sum(vals) / len(vals) for cid, vals in deltas.items()
        }
        avg_w = (
            sum(weighted_deltas) / len(weighted_deltas)
            if weighted_deltas
            else 0.0
        )
        new_word_effects[word] = WordEffect(
            tested=len(group),
            avg_delta=avg_delta,
            avg_weighted_delta=avg_w,
            stability=_classify_stability(avg_w, repeats),
            conflicts_with=kb.word_effects.get(
                word, WordEffect()
            ).conflicts_with,
            synergizes_with=kb.word_effects.get(
                word, WordEffect()
            ).synergizes_with,
        )
    kb.word_effects = new_word_effects

    # Combo effects.
    new_combos: dict[str, ComboEffect] = {}
    for words_tuple, group in combo_acc.items():
        weighted_deltas = [t.weighted_delta for t in group]
        avg = sum(weighted_deltas) / len(weighted_deltas) if weighted_deltas else 0.0
        sum_individual = sum(
            kb.word_effects.get(w, WordEffect()).avg_weighted_delta
            for w in words_tuple
        )
        if avg - sum_individual > _SYNERGY_BONUS:
            verdict = "SYNERGY"
        elif sum_individual - avg > _SYNERGY_BONUS:
            verdict = "CONFLICT"
        else:
            verdict = "NEUTRAL"
        new_combos[" + ".join(words_tuple)] = ComboEffect(
            tested=len(group),
            avg_total_delta=avg,
            individual_sum_delta=sum_individual,
            synergy_verdict=verdict,
        )

        if verdict == "CONFLICT":
            for i, a in enumerate(words_tuple):
                for b in words_tuple[i + 1 :]:
                    if a in kb.word_effects and b not in kb.word_effects[a].conflicts_with:
                        kb.word_effects[a].conflicts_with.append(b)
                    if b in kb.word_effects and a not in kb.word_effects[b].conflicts_with:
                        kb.word_effects[b].conflicts_with.append(a)
        elif verdict == "SYNERGY":
            for i, a in enumerate(words_tuple):
                for b in words_tuple[i + 1 :]:
                    if a in kb.word_effects and b not in kb.word_effects[a].synergizes_with:
                        kb.word_effects[a].synergizes_with.append(b)
                    if b in kb.word_effects and a not in kb.word_effects[b].synergizes_with:
                        kb.word_effects[b].synergizes_with.append(a)
    kb.combo_effects = new_combos

    # Refresh visual_rules ("do" / "dont") from stable words.
    do_rules: list[VisualRule] = []
    dont_rules: list[VisualRule] = []
    for word, eff in kb.word_effects.items():
        if eff.stability == "STABLE_POSITIVE":
            top = sorted(
                eff.avg_delta.items(), key=lambda x: x[1], reverse=True
            )[:2]
            rationale = ", ".join(f"{c}:+{v:.1f}" for c, v in top if v > 0)
            do_rules.append(
                VisualRule(
                    rule=f"Использовать '{word}' ({rationale})",
                    supporting_evidence=f"{eff.tested} tests, weighted +{eff.avg_weighted_delta:.2f}",
                )
            )
        elif eff.stability == "STABLE_NEGATIVE":
            dont_rules.append(
                VisualRule(
                    rule=f"Избегать '{word}'",
                    supporting_evidence=f"{eff.tested} tests, weighted {eff.avg_weighted_delta:+.2f}",
                )
            )
    kb.visual_rules = {"do": do_rules, "dont": dont_rules}
    return kb


__all__ = ["rebuild_word_effects"]
