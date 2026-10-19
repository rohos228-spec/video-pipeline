"""Уточнение s0–s7: части только из нарезки 8, замороженный перенос не тронут."""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator.rag.chunking import split_prompt
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.store import chunk_id_for
from evals.orchestrator.label_expand import EXPANDED_PATH, expand_labels
from evals.orchestrator.label_transfer import AMBIGUOUS_PATH, QUERY_PATH, _templates


def test_s0_s7_labels_match_eight_part_split_and_the_index(tmp_path: Path) -> None:
    original = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    live_29 = next(query for query in original if query["id"] == "live-29")
    document, fresh = expand_labels(original)
    committed = json.loads(EXPANDED_PATH.read_text(encoding="utf-8"))
    assert committed["queries"] == document["queries"]
    assert fresh == []
    again = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    assert next(query for query in again if query["id"] == "live-29") == live_29
    ambiguous = json.loads(AMBIGUOUS_PATH.read_text(encoding="utf-8"))
    assert ambiguous["count"] == 12
    templates = _templates(Path(__file__).resolve().parents[2])
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
    for query, source in zip(document["queries"], original, strict=True):
        assert query["text"] == source["text"]
        by_source: dict[str, set[str]] = {}
        for item in query["relevant"]:
            seen += 1
            assert item["grade"] == 1
            assert item["chunk_id"] == chunk_id_for(item["corpus"], item["source_id"], item["part_key"])
            assert item["chunk_id"] in indexed
            if item["corpus"] == "prompts":
                by_source.setdefault(item["source_id"], set()).add(item["part_key"])
        labeled_sources = {
            item["source_id"]
            for item in source.get("relevant") or []
            if item.get("corpus") == "prompts"
        }
        for source_id in labeled_sources:
            expected = {key for key, _body in split_prompt(templates[source_id], max_parts=8)}
            assert by_source[source_id] == expected
            assert any(key not in {"s0", "s1", "s2"} for key in expected)
    assert seen > 0
