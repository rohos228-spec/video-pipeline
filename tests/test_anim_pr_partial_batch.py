"""Partial GPT anim_pr batch must not hard-fail the step."""

from __future__ import annotations


def _classify(batch_nums: set[int], filled: set[int]) -> str:
    """Mirror of _save_anim_pr_batch progress gate."""
    still = batch_nums - filled
    if not still:
        return "complete"
    if batch_nums & filled:
        return "partial"
    return "empty"


def test_partial_batch_is_progress_not_empty() -> None:
    assert _classify({5, 6, 7, 8, 9, 10}, {5}) == "partial"


def test_full_batch_complete() -> None:
    assert _classify({5, 6, 7, 8, 9, 10}, {5, 6, 7, 8, 9, 10}) == "complete"


def test_zero_overlap_is_empty_fail() -> None:
    assert _classify({5, 6, 7, 8, 9, 10}, {99}) == "empty"
    assert _classify({5, 6, 7, 8, 9, 10}, set()) == "empty"


def test_batch_size_is_six() -> None:
    from app.services.animation_prompt_gpt import BATCH_SIZE

    assert BATCH_SIZE == 6
