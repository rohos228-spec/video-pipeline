from app.services.adaptive_llm_batches import (
    MAX_SPLIT_LEVEL,
    next_split_level,
    split_in_half,
)


def test_next_split_is_1_then_2_then_4() -> None:
    assert next_split_level(1) == 2
    assert next_split_level(2) == 4
    assert next_split_level(4) is None
    assert next_split_level(MAX_SPLIT_LEVEL) is None


def test_split_in_half_keeps_order() -> None:
    items = list(range(5))
    parts = split_in_half(items)
    assert parts == [[0, 1, 2], [3, 4]]
    assert split_in_half([1]) == [[1]]
    assert split_in_half([]) == []
