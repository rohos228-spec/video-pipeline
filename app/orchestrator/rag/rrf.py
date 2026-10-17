"""Reciprocal Rank Fusion, k=60 (как в плане WS6)."""

from __future__ import annotations


def rrf_merge(
    rank_lists: list[list[str]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> dict[str, float]:
    """``rank_lists`` — списки id от лучшего к худшему. Выше score — лучше.

    ``weights`` — множитель списка (лексика, затем вектор). По умолчанию 1 и 1.
    """
    scores: dict[str, float] = {}
    for list_index, ranked in enumerate(rank_lists):
        weight = 1.0 if weights is None else float(weights[list_index])
        if weight == 0.0:
            continue
        for index, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + index)
    return scores
