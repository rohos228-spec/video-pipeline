"""Reciprocal Rank Fusion, k=60 (как в плане WS6)."""

from __future__ import annotations


def rrf_merge(rank_lists: list[list[str]], *, k: int = 60) -> dict[str, float]:
    """``rank_lists`` — списки id от лучшего к худшему. Выше score — лучше."""
    scores: dict[str, float] = {}
    for ranked in rank_lists:
        for index, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + index)
    return scores
