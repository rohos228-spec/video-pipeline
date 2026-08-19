"""LLM batching: 1 call first, on error split 2, on error split 4.

Do not pre-slice into 5 packs or voiceover/3500 packs.
"""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")

# After a failed full call: 2 packs. After a failed half: 4 packs (split that half).
NEXT_SPLIT = {1: 2, 2: 4}
MAX_SPLIT_LEVEL = 4


def next_split_level(level: int) -> int | None:
    """None = cannot split further."""
    nxt = NEXT_SPLIT.get(int(level))
    if nxt is None or nxt > MAX_SPLIT_LEVEL:
        return None
    return nxt


def split_in_half(items: Sequence[T]) -> list[list[T]]:
    """Two almost-equal packs, order kept. One item → no split."""
    seq = list(items)
    if len(seq) <= 1:
        return [seq] if seq else []
    mid = (len(seq) + 1) // 2
    return [seq[:mid], seq[mid:]]
