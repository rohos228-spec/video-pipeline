from app.bots.outsee import _prompt_id_search_tokens


def test_prompt_id_search_tokens_basic() -> None:
    toks = _prompt_id_search_tokens("[ID: P8-EXCEL-c01-a7f2b01c]")
    assert "[ID: P8-EXCEL-c01-a7f2b01c]" in toks
    assert "P8-EXCEL-c01-a7f2b01c" in toks
    assert "a7f2b01c" in toks


def test_prompt_id_search_tokens_uniquified_retry() -> None:
    toks = _prompt_id_search_tokens("[ID: P8-EXCEL-c01-a7f2b01c r2a1]")
    assert "[ID: P8-EXCEL-c01-a7f2b01c r2a1]" in toks
    assert "P8-EXCEL-c01-a7f2b01c" in toks
    assert "a7f2b01c" in toks
