"""Реранкер не подменяет выдачу: RRF с BM25 и защита сильного топ-1."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import SourceDoc


class _PreferWeak:
    reranker_id = "prefer-weak"

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [0.0 if "ЛИДЕР" in passage else 10.0 for passage in passages]


def _engine(tmp_path: Path) -> RagEngine:
    docs = [
        SourceDoc(
            corpus="nodes",
            source_id="n_leader",
            title="Лидер",
            text="ЛИДЕР якорь " * 12,
        ),
        SourceDoc(
            corpus="nodes",
            source_id="n_weak",
            title="Слабый",
            text="якорь упомянут один раз",
        ),
    ]
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"nodes": docs, "prompts": [], "rules": [], "history": []},
        scan_git=False,
        reranker=_PreferWeak(),
    )
    engine.reindex("nodes")
    return engine


def test_rrf_fusion_does_not_hand_the_list_to_the_reranker(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        replaced = engine.retrieve("якорь", ["nodes"], top_k=2, rerank=True, rerank_fusion="replace")
        fused = engine.retrieve(
            "якорь",
            ["nodes"],
            top_k=2,
            rerank=True,
            rerank_fusion="rrf",
            rerank_fusion_weights=(1.5, 1.0),
        )
    finally:
        engine.close()
    assert replaced[0].source_id == "n_weak"
    assert fused[0].source_id == "n_leader"


def test_strong_bm25_leader_stays_first_only_above_the_threshold(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        lexical = engine.retrieve("якорь", ["nodes"], top_k=2, channel="bm25")
        assert lexical[0].source_id == "n_leader"
        ratio = lexical[0].score_bm25 / lexical[1].score_bm25
        kwargs = {
            "rerank": True,
            "rerank_fusion": "rrf",
            "rerank_fusion_weights": (1.0, 8.0),
        }
        held = engine.retrieve("якорь", ["nodes"], top_k=2, bm25_protect=ratio * 0.99, **kwargs)
        released = engine.retrieve("якорь", ["nodes"], top_k=2, bm25_protect=ratio * 1.01, **kwargs)
        with pytest.raises(ValueError, match="bm25_protect"):
            engine.retrieve("якорь", ["nodes"], top_k=2, bm25_protect=-1)
    finally:
        engine.close()
    assert ratio > 1
    assert held[0].source_id == "n_leader"
    assert released[0].source_id == "n_weak"
