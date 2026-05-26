import pytest

from app.bots.outsee import OutseeImageError, _verify_prompt_length_before_send
from app.generation_options import OUTSEE_PROMPT_MAX_CHARS, prepend_gen_id


def test_outsee_prompt_max_constant() -> None:
    assert OUTSEE_PROMPT_MAX_CHARS == 5000


def test_verify_prompt_length_rejects_over_limit() -> None:
    big = "x" * (OUTSEE_PROMPT_MAX_CHARS + 1)
    with pytest.raises(OutseeImageError) as exc:
        _verify_prompt_length_before_send(big, where="test")
    assert "5000" in str(exc.value)
    assert exc.value.context.get("prompt_len") == OUTSEE_PROMPT_MAX_CHARS + 1


def test_verify_prompt_length_allows_at_limit() -> None:
    ok = "y" * OUTSEE_PROMPT_MAX_CHARS
    _verify_prompt_length_before_send(ok, where="test")


def test_prepend_gen_id_counts_toward_limit() -> None:
    body = "z" * 4980
    prefix = "[ID: P8-F1-9169c5f6]"
    full = prepend_gen_id(body, prefix)
    assert len(full) > OUTSEE_PROMPT_MAX_CHARS
