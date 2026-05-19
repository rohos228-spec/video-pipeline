"""Tests for the hard 4800-char prompt limit and PromptTooLongError."""

from __future__ import annotations

import pytest

from app.services.visual_lab.limits import (
    ID_PREFIX_RESERVE,
    MAX_PROMPT_CHARS,
    PromptTooLongError,
    check_prompt_length,
    soft_limit,
)


def test_constants_match_spec() -> None:
    assert MAX_PROMPT_CHARS == 4800
    assert ID_PREFIX_RESERVE == 80
    assert soft_limit() == MAX_PROMPT_CHARS - ID_PREFIX_RESERVE


def test_short_prompt_ok() -> None:
    check_prompt_length("short prompt")


def test_exactly_soft_limit_ok() -> None:
    text = "x" * soft_limit()
    check_prompt_length(text)


def test_over_soft_limit_raises() -> None:
    text = "x" * (soft_limit() + 1)
    with pytest.raises(PromptTooLongError) as exc_info:
        check_prompt_length(text)
    assert exc_info.value.length == soft_limit() + 1
    assert exc_info.value.limit == soft_limit()


def test_over_hard_limit_raises() -> None:
    text = "x" * (MAX_PROMPT_CHARS + 100)
    with pytest.raises(PromptTooLongError):
        check_prompt_length(text)


def test_without_id_reserve_uses_hard_limit() -> None:
    text = "x" * MAX_PROMPT_CHARS
    check_prompt_length(text, include_id_reserve=False)
    with pytest.raises(PromptTooLongError):
        check_prompt_length("x" * (MAX_PROMPT_CHARS + 1), include_id_reserve=False)
