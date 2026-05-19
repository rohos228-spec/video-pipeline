"""A/B and combo word tests.

Two kinds of tests:

* **A/B test** — take a base iteration, mutate the prompt by ADD/REMOVE/
  REPLACE on a single word, generate + analyze, compute deltas.
* **Combo test** — same idea but mutates multiple words at once.

Each test is repeated up to ``REPEAT_K`` times to detect unstable words
(deltas vary > _UNSTABLE_SPREAD). The repeat scores feed into the
``stability`` classifier in :mod:`knowledge_update`.

This module deliberately keeps the *test orchestration* (generate +
analyze loop) separate from the *knowledge update* (aggregation), which
lives in :mod:`knowledge_update`. Both are pure data transforms over the
on-disk JSON state, easy to unit-test without a browser.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from loguru import logger

from app.services.visual_lab.criteria import weighted_score
from app.services.visual_lab.knowledge_update import rebuild_word_effects
from app.services.visual_lab.models import IterDoc, WordTest
from app.services.visual_lab.storage import LabStorage

REPEAT_K_DEFAULT: int = 1  # set 3 to actually re-roll each word


def _build_test_prompt(
    base_prompt: str, *, operation: str, word: str, replacement_for: str | None = None
) -> str:
    """Apply ADD/REMOVE/REPLACE on a base prompt.

    Naive string ops — fine for typical short keywords like ``"crisp pixel
    edges"`` or ``"smooth"`` — kept simple and predictable.
    """
    if operation == "ADD":
        # Append at the end with a comma + space, idempotent.
        if word.strip().lower() in base_prompt.lower():
            return base_prompt
        sep = ", " if base_prompt and not base_prompt.rstrip().endswith(",") else " "
        return f"{base_prompt.rstrip(' ,')}{sep}{word.strip()}"
    if operation == "REMOVE":
        return _remove_token_ci(base_prompt, word)
    if operation == "REPLACE":
        if not replacement_for:
            raise ValueError("REPLACE requires replacement_for")
        return _remove_token_ci(base_prompt, replacement_for).rstrip(" ,") + (
            ", " + word.strip()
        )
    raise ValueError(f"unknown operation: {operation}")


def _remove_token_ci(text: str, token: str) -> str:
    """Case-insensitive token removal that preserves surrounding punctuation."""
    needle = token.strip().lower()
    if not needle:
        return text
    lower = text.lower()
    out = []
    i = 0
    while i < len(text):
        if lower.startswith(needle, i):
            i += len(needle)
            # Skip a trailing comma+space pair if present.
            while i < len(text) and text[i] in ", ":
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out).strip(" ,")


def make_test_prompt(
    base_iter_doc: IterDoc,
    *,
    operation: str,
    word: str,
    replacement_for: str | None = None,
) -> str:
    """Public helper used by the runner / unit tests."""
    return _build_test_prompt(
        base_iter_doc.prompt.text,
        operation=operation,
        word=word,
        replacement_for=replacement_for,
    )


async def record_word_test(
    storage: LabStorage,
    *,
    base_iter: IterDoc,
    test_iter: IterDoc,
    word: str,
    operation: str,
    target_criteria: list[str],
    hypothesis_id: int | None,
    next_test_id: Callable[[], int] | None = None,
) -> WordTest:
    """Append a completed word-test row and refresh the knowledge base."""
    base_scores = (
        base_iter.analysis.scores if base_iter.analysis else {}
    )
    test_scores = (
        test_iter.analysis.scores if test_iter.analysis else {}
    )
    common = set(base_scores) & set(test_scores)
    delta_per = {
        cid: float(test_scores[cid]) - float(base_scores[cid]) for cid in common
    }
    weighted_delta = weighted_score(
        {cid: float(test_scores[cid]) for cid in test_scores}
    ) - weighted_score(
        {cid: float(base_scores[cid]) for cid in base_scores}
    )

    tests = storage.load_word_tests()
    new_id = next_test_id() if next_test_id else (max((t.id for t in tests), default=0) + 1)

    test = WordTest(
        id=new_id,
        hypothesis_id=hypothesis_id,
        word=word,
        operation=operation,  # type: ignore[arg-type]
        target_criteria=target_criteria,
        base_iter=base_iter.iter,
        test_iter=test_iter.iter,
        base_scores=base_scores,
        test_scores=test_scores,
        delta_per_criterion=delta_per,
        weighted_delta=weighted_delta,
        repeat_scores=[],
        stability="UNTESTED",
        verdict="IMPROVED" if weighted_delta > 0.2
        else ("REGRESSED" if weighted_delta < -0.2 else "NEUTRAL"),
    )
    tests.append(test)
    storage.save_word_tests(tests)

    kb = storage.load_knowledge()
    rebuild_word_effects(kb, tests)
    if hypothesis_id is not None:
        for h in kb.hypotheses:
            if h.id == hypothesis_id:
                h.status = (
                    "CONFIRMED" if weighted_delta > 0.5
                    else ("REJECTED" if weighted_delta < -0.5 else "TESTING")
                )
                h.evidence = (
                    f"weighted_delta={weighted_delta:+.2f} from word_test #{new_id}"
                )
    storage.save_knowledge(kb)

    logger.info(
        "visual_lab.word_test[{}] #{} word={!r} op={} weighted_delta={:+.2f} verdict={}",
        storage.slug,
        new_id,
        word,
        operation,
        weighted_delta,
        test.verdict,
    )
    await asyncio.sleep(0)
    return test


__all__ = [
    "REPEAT_K_DEFAULT",
    "make_test_prompt",
    "record_word_test",
]
