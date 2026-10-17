"""Кэш эмбеддингов и агрегация частей промпта. Веса не качаются."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import SourceDoc


class _AlphaWins:
    reranker_id = "alpha-wins"

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [1.0 if "альфа" in passage else 0.0 for passage in passages]


def _docs() -> list[SourceDoc]:
    return [
        SourceDoc(corpus="prompts", source_id="n_a", title="A0", text="альфа ноль", part_key="s0"),
        SourceDoc(corpus="prompts", source_id="n_a", title="A1", text="альфа один", part_key="s1"),
        SourceDoc(corpus="prompts", source_id="n_a", title="A2", text="альфа два", part_key="s2"),
        SourceDoc(corpus="prompts", source_id="n_b", title="B", text="бета чужая", part_key="s0"),
        SourceDoc(corpus="rules", source_id="rules.md", title="R", text="правило отдельно", part_key="rule-1"),
    ]


def test_query_and_chunk_embeddings_are_cached(tmp_path: Path) -> None:
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": _docs()[:4], "rules": _docs()[4:], "nodes": [], "history": []},
        scan_git=False,
    )
    calls = {"query": 0, "fetch": 0}
    real_query = engine.embedder.embed_query
    real_fetch = engine.store.fetch_chunks

    def query(text: str) -> list[float]:
        calls["query"] += 1
        return real_query(text)

    def fetch(corpora: list[str]):
        calls["fetch"] += 1
        return real_fetch(corpora)

    engine.embedder.embed_query = query  # type: ignore[method-assign]
    engine.store.fetch_chunks = fetch  # type: ignore[method-assign]
    try:
        engine.reindex(["prompts", "rules"])
        first = engine.retrieve("альфа", ["prompts", "rules"], top_k=3, rerank=False)
        second = engine.retrieve("альфа", ["prompts", "rules"], top_k=3, rerank=False)
    finally:
        engine.close()
    assert calls["query"] == 1
    assert calls["fetch"] == 1
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert "_embedding" not in first[0].metadata


def test_prompt_expand_keeps_parts_of_the_winning_prompt_together(tmp_path: Path) -> None:
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": _docs()[:4], "rules": [], "nodes": [], "history": []},
        scan_git=False,
        reranker=_AlphaWins(),
    )
    try:
        engine.reindex("prompts")
        found = engine.retrieve(
            "альфа",
            ["prompts"],
            top_k=3,
            rerank=True,
            rerank_pool=4,
            aggregate="prompt_expand",
        )
    finally:
        engine.close()
    assert [chunk.source_id for chunk in found] == ["n_a", "n_a", "n_a"]
    assert "_embedding" not in found[0].metadata
