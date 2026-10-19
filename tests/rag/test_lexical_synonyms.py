"""Синонимы BM25 строятся из полей и названий нод, не из текста запроса."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.lexical import registry_synonyms, spelling_synonyms
from app.orchestrator.rag.types import SourceDoc


def test_spelling_synonym_links_a_field_piece_to_a_node_word() -> None:
    table = spelling_synonyms(
        ["поле промт_картинки"],
        ["GPT: промпты кадров"],
    )
    assert table["промт"] == frozenset({"промпт"})
    assert "промт" in table["промпт"]


def test_inflection_and_unrelated_neighbors_are_not_synonyms() -> None:
    table = spelling_synonyms(
        ["тип_стыка", "смена_сцены"],
        ["стыкуют срезы", "сцена и смена и контракт"],
    )
    assert "сцена" not in table
    assert "смена" not in table
    assert "стык" not in table


def test_registry_synonyms_come_from_node_fields_not_from_queries() -> None:
    table = registry_synonyms()
    assert table["промт"] == frozenset({"промпт"})


def test_bm25_finds_a_field_identifier_from_the_node_word(tmp_path: Path) -> None:
    docs = [
        SourceDoc(
            corpus="prompts",
            source_id="n_frames",
            title="Кадры",
            text="Поле промт_картинки заполняет мастер-кадр.",
            part_key="s6",
        ),
        SourceDoc(
            corpus="prompts",
            source_id="n_other",
            title="Другое",
            text="Здесь только картинки без этого поля.",
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
        found = engine.retrieve("промпты картинок", ["prompts"], top_k=2, channel="bm25")
    finally:
        engine.close()
    assert found
    assert found[0].source_id == "n_frames"
