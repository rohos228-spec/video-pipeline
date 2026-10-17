"""expand_tail держит top-5 как none и заполняет только хвост."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.engine import RagEngine, _aggregate_parts
from app.orchestrator.rag.types import Chunk, SourceDoc


def _chunk(chunk_id: str, source_id: str, *, corpus: str = "prompts") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        corpus=corpus,
        source_id=source_id,
        title=chunk_id,
        text=chunk_id,
        metadata={"part_key": chunk_id},
    )


def test_expand_tail_top5_matches_none_and_tail_is_sibling_parts() -> None:
    chunks = [
        _chunk("a0", "n_a"),
        _chunk("b0", "n_b"),
        _chunk("a1", "n_a"),
        _chunk("c0", "n_c"),
        _chunk("rule", "rules.md", corpus="rules"),
        _chunk("a2", "n_a"),
        _chunk("a3", "n_a"),
        _chunk("b1", "n_b"),
        _chunk("c1", "n_c"),
    ]
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    ordered = ["a0", "b0", "a1", "c0", "rule", "late", "a2"]
    scores = {
        "a0": 10.0,
        "b0": 9.0,
        "a1": 8.0,
        "c0": 7.0,
        "rule": 6.0,
        "a2": 4.0,
        "a3": 3.5,
        "b1": 5.0,
        "c1": 1.0,
        "late": 2.0,
    }
    none = _aggregate_parts(ordered, scores, by_id, "none")
    tail = _aggregate_parts(ordered, scores, by_id, "expand_tail")
    assert none[:5] == ["a0", "b0", "a1", "c0", "rule"]
    assert tail[:5] == none[:5]
    assert tail[5:8] == ["a2", "a3", "b1"]


def test_expand_tail_retrieve_keeps_the_same_top5(tmp_path: Path) -> None:
    prompts = [
        SourceDoc(corpus="prompts", source_id="n_a", title=f"A{index}", text=f"камера постановка часть {index}", part_key=f"s{index}")
        for index in range(8)
    ]
    prompts.append(SourceDoc(corpus="prompts", source_id="n_b", title="B", text="звук и музыка", part_key="s0"))
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": prompts, "rules": [], "nodes": [], "history": []},
        scan_git=False,
    )
    try:
        engine.reindex("prompts")
        kwargs = {
            "rerank": False,
            "rrf_k": 20,
            "rrf_weights": (0.5, 1.5),
            "rerank_pool": 12,
        }
        none = engine.retrieve("камера постановка", ["prompts"], top_k=8, aggregate="none", **kwargs)
        tail = engine.retrieve("камера постановка", ["prompts"], top_k=8, aggregate="expand_tail", **kwargs)
    finally:
        engine.close()
    assert [chunk.chunk_id for chunk in tail[:5]] == [chunk.chunk_id for chunk in none[:5]]
    assert len(none) >= 5
