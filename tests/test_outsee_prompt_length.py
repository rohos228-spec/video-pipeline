import pytest

from app.bots.outsee import (
    OutseeImageError,
    OutseePromptTooLongError,
    _raise_outsee_failure,
    _verify_prompt_length_before_send,
    outsee_error_kind,
)
from app.generation_options import (
    OUTSEE_PROMPT_MAX_CHARS,
    prepend_gen_id,
    strip_prompt_id_lines,
)


def test_outsee_prompt_max_constant() -> None:
    assert OUTSEE_PROMPT_MAX_CHARS == 4900


def test_verify_prompt_length_rejects_over_limit() -> None:
    big = "x" * (OUTSEE_PROMPT_MAX_CHARS + 1)
    with pytest.raises(OutseePromptTooLongError) as exc:
        _verify_prompt_length_before_send(big, where="test")
    assert "4900" in str(exc.value)
    assert exc.value.context.get("error_kind") == "length"
    assert exc.value.context.get("prompt_len") == OUTSEE_PROMPT_MAX_CHARS + 1


def test_verify_prompt_length_allows_at_limit() -> None:
    ok = "y" * OUTSEE_PROMPT_MAX_CHARS
    _verify_prompt_length_before_send(ok, where="test")


def test_prepend_gen_id_counts_toward_limit() -> None:
    body = "z" * 4980
    prefix = "[ID: P8-F1-9169c5f6]"
    full = prepend_gen_id(body, prefix)
    assert len(full) > OUTSEE_PROMPT_MAX_CHARS


def test_prepend_gen_id_strips_duplicate_id_lines() -> None:
    body = (
        "[ID: P17-F90-dda7487c]\n\n"
        "[ID: P17-F90-dda7487c r1a2]\n\n"
        "Cinematic wide shot of a lab."
    )
    prefix = "[ID: P17-F90-fd10c7d1]"
    full = prepend_gen_id(body, prefix)
    assert full.count("[ID:") == 1
    assert full.startswith(prefix)
    assert "Cinematic wide shot" in full
    assert "dda7487c" not in full
    assert "r1a2" not in full


def test_strip_prompt_id_lines_only() -> None:
    raw = "[ID: P1-F2-abc]\n\nhello"
    assert strip_prompt_id_lines(raw) == "hello"
