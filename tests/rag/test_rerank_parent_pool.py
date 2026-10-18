"""Перед реранкером у родителя один чанк — с лучшей оценкой. Хвост списка добирает пул."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.engine import RagEngine, _unique_parent_pool
from app.orchestrator.rag.types import Chunk, SourceDoc


def _chunk(chunk_id: str, source_id: str, part_key: str, *, parent: str = "") -> Chunk:
    metadata = {"part_key": part_key}
    if parent:
        metadata["parent_part_key"] = parent
        metadata["part_key"] = f"{parent}~0"
    return Chunk(
        chunk_id=chunk_id,
        corpus="prompts",
        source_id=source_id,
        title="t",
        text=chunk_id,
        metadata=metadata,
    )


def test_unique_parents_keep_the_base_prefix() -> None:
    chunks = [
        _chunk("a", "n_a", "s0"),
        _chunk("b", "n_b", "s0"),
        _chunk("c", "n_c", "s1"),
    ]
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    ordered = ["a", "b", "c"]
    scores = {"a": 0.9, "b": 0.5, "c": 0.4}
    assert _unique_parent_pool(ordered, scores, by_id, 2) == ["a", "b"]


def test_duplicate_parent_loses_to_the_better_score_and_frees_a_slot() -> None:
    parent = _chunk("parent", "n_asm", "s5")
    window = _chunk("window", "n_asm", "s5", parent="s5")
    other = _chunk("other", "n_camera", "s0")
    by_id = {chunk.chunk_id: chunk for chunk in (window, parent, other)}
    # Окно раньше в списке, но оценка родителя выше — в пул идёт родитель.
    ordered = ["window", "parent", "other"]
    scores = {"window": 0.4, "parent": 0.8, "other": 0.3}
    assert _unique_parent_pool(ordered, scores, by_id, 2) == ["parent", "other"]


def test_rerank_pool_skips_the_worse_window(tmp_path: Path) -> None:
    parent = SourceDoc(
        corpus="prompts",
        source_id="n_excel_gpt_sd_asm",
        title="Сборка",
        text="Общий закон секции крючок.",
        part_key="s5",
    )
    window = SourceDoc(
        corpus="prompts",
        source_id="n_excel_gpt_sd_asm",
        title="Сборка",
        text="Короткое окно.",
        part_key="s5~0",
        metadata={"parent_part_key": "s5"},
    )
    other = SourceDoc(
        corpus="prompts",
        source_id="n_camera",
        title="Камера",
        text="Другой текст про двор и крючок.",
        part_key="s0",
    )

    class _Recording:
        reranker_id = "recording"

        def __init__(self) -> None:
            self.passages: list[str] = []

        def score(self, query: str, passages: list[str]) -> list[float]:
            self.passages = list(passages)
            return [float(len(passages) - index) for index in range(len(passages))]

    recorder = _Recording()
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": [parent, window, other], "nodes": [], "rules": [], "history": []},
        scan_git=False,
        reranker=recorder,
    )
    try:
        engine.reindex("prompts")
        engine.retrieve(
            "крючок",
            ["prompts"],
            top_k=2,
            rerank=True,
            rerank_pool=2,
            rerank_fusion="replace",
            aggregate="none",
        )
    finally:
        engine.close()
    assert len(recorder.passages) == 2
    assert sum("Сборка" in passage for passage in recorder.passages) == 1
    assert sum("Камера" in passage for passage in recorder.passages) == 1
