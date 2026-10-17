"""Перенос меток контура B: новый chunk_id существует, неоднозначные не подмешаны."""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator.rag.chunking import split_prompt
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.store import chunk_id_for
from evals.orchestrator.label_transfer import (
    AMBIGUOUS_PATH,
    QUERY_PATH,
    TRANSFERRED_PATH,
    prompt_sections,
    transfer_labels,
)


def test_prompt_sections_match_split_prompt() -> None:
    text = "\n".join(f"## Секция {index}\nтекст {index}" for index in range(10))
    for max_parts in (3, 8):
        laid = prompt_sections(text, max_parts=max_parts)
        assert [(key, body) for key, body, _indexes in laid] == split_prompt(text, max_parts=max_parts)


def test_transferred_labels_point_at_indexed_chunks(tmp_path: Path) -> None:
    original = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    live_29 = next(query for query in original if query["id"] == "live-29")
    transferred, ambiguous = transfer_labels(original)
    committed = json.loads(TRANSFERRED_PATH.read_text(encoding="utf-8"))
    committed_ambiguous = json.loads(AMBIGUOUS_PATH.read_text(encoding="utf-8"))
    assert committed["queries"] == transferred["queries"]
    assert committed_ambiguous["count"] == len(ambiguous["items"])
    assert committed_ambiguous["items"] == ambiguous["items"]
    again = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    assert next(query for query in again if query["id"] == "live-29") == live_29
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=Path(__file__).resolve().parents[2] / "docs" / "fable" / "user_rules.md",
        repo_root=Path(__file__).resolve().parents[2],
        scan_git=True,
    )
    try:
        engine.reindex(["rules", "nodes", "prompts"])
        indexed = {chunk.chunk_id for chunk in engine.store.fetch_chunks(["rules", "nodes", "prompts"])}
    finally:
        engine.close()
    seen = 0
    for query in transferred["queries"]:
        for item in query["relevant"]:
            seen += 1
            assert item["grade"] == 1
            assert item["chunk_id"] == chunk_id_for(item["corpus"], item["source_id"], item["part_key"])
            assert item["chunk_id"] in indexed
    assert seen > 0
    ambiguous_ids = {item["old_chunk_id"] for item in ambiguous["items"]}
    transferred_from = {
        old
        for query in transferred["queries"]
        for item in query["relevant"]
        for old in item["from"]
    }
    assert ambiguous_ids.isdisjoint(transferred_from)
