"""Слияние none и prompt_expand не подгоняется под id запросов."""

from __future__ import annotations

from app.orchestrator.rag.engine import _none_cand_expand_rerank, _rrf_none_expand
from app.orchestrator.rag.types import Chunk


def _chunk(chunk_id: str, source_id: str, text: str, *, corpus: str = "prompts") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        corpus=corpus,
        source_id=source_id,
        title=chunk_id,
        text=text,
        metadata={"part_key": chunk_id},
    )


class _ExpandTextWins:
    def score(self, query: str, passages: list[str]) -> list[float]:
        return [1.0 if "целевая-часть" in passage else 0.0 for passage in passages]


def test_none_cand_expand_rerank_reorders_only_the_none_pool() -> None:
    chunks = [
        _chunk("b0", "n_b", "бета"),
        _chunk("a0", "n_a", "альфа"),
        _chunk("a1", "n_a", "целевая-часть"),
        _chunk("out", "n_z", "целевая-часть снаружи пула"),
    ]
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    ordered = ["b0", "a0", "out"]
    ranked = _none_cand_expand_rerank(ordered, by_id, "запрос", _ExpandTextWins(), 2)
    assert ranked[:2] == ["a0", "b0"]
    assert ranked[2] == "out"
    assert set(ranked[:2]) == {"a0", "b0"}


def test_rrf_none_expand_keeps_none_top5() -> None:
    none = ["a", "b", "c", "d", "e", "f", "g"]
    expand = ["g", "f", "a", "b", "c", "d", "e"]
    fused = _rrf_none_expand(none, expand, k=20)
    assert fused[:5] == none[:5]
    assert fused[5] == "g"
    late_hit = ["hit", "q", "r", "s", "t", "u"]
    expand_drops_hit = ["u", "q", "r", "s", "t", "hit"]
    kept = _rrf_none_expand(late_hit, expand_drops_hit, k=60)
    assert kept[0] == "hit"
    assert "hit" in kept[:5]
