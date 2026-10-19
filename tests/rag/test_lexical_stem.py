"""BM25: snake_case режется, русские формы сходятся к одной основе."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.lexical import lexical_tokens
from app.orchestrator.rag.types import SourceDoc


def test_snake_case_stem_matches_the_inflected_verb() -> None:
    left = set(lexical_tokens("тип_стыка"))
    right = set(lexical_tokens("стыкуют"))
    assert left & right
    assert "стык" in left & right


def test_picture_genitive_shares_a_lemma_with_the_field_word() -> None:
    assert set(lexical_tokens("картинок")) & set(lexical_tokens("картинки"))


def test_bm25_finds_a_snake_case_identifier_from_an_inflected_query(tmp_path: Path) -> None:
    docs = [
        SourceDoc(
            corpus="prompts",
            source_id="n_asm",
            title="Сборка",
            text="Поле тип_стыка задаёт склейку соседних срезов.",
            part_key="s3",
        ),
        SourceDoc(
            corpus="prompts",
            source_id="n_other",
            title="Другое",
            text="Здесь нет нужного идентификатора, только общий рассказ о сценах.",
            part_key="s0",
        ),
    ]
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": docs, "nodes": [], "rules": [], "history": []},
        scan_git=False,
    )
    try:
        engine.reindex("prompts")
        found = engine.retrieve("чем стыкуют чужие срезы", ["prompts"], top_k=2, channel="bm25")
    finally:
        engine.close()
    assert found
    assert found[0].source_id == "n_asm"
