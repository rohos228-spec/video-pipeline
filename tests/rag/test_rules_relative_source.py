"""Метки правил резолвятся в чанк индекса по пути относительно корня репо."""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.loaders import RULES_SOURCE_ID, load_rules
from app.orchestrator.rag.store import chunk_id_for
from app.project_root import find_project_root

ROOT = find_project_root()
LABELS = ROOT / "evals" / "orchestrator" / "datasets" / "precision_live_labels_s0_s7.json"
TRANSFERRED = ROOT / "evals" / "orchestrator" / "datasets" / "precision_live_labels_transferred.json"
RULES = {
    "live-43": "rule-1",
    "live-44": "rule-2",
    "live-45": "rule-3",
    "live-46": "rule-4",
    "live-47": "rule-5",
    "live-48": "rule-6",
    "live-49": "rule-7",
    "live-50": "rule-8",
}


def test_loader_uses_a_repo_relative_rules_path() -> None:
    docs = load_rules(Path("/workspace") / "docs" / "fable" / "user_rules.md")
    assert docs
    assert {doc.source_id for doc in docs} == {RULES_SOURCE_ID}
    assert not RULES_SOURCE_ID.startswith("/")


def test_every_label_resolves_to_an_indexed_chunk(tmp_path: Path) -> None:
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        repo_root=ROOT,
        rules_path=ROOT / "docs" / "fable" / "user_rules.md",
        scan_git=True,
    )
    try:
        engine.reindex(["rules", "nodes", "prompts"])
        indexed = {chunk.chunk_id for chunk in engine.store.fetch_chunks(["rules", "nodes", "prompts"])}
    finally:
        engine.close()
    for path in (LABELS, TRANSFERRED):
        document = json.loads(path.read_text(encoding="utf-8"))
        seen_rules: dict[str, str] = {}
        for query in document["queries"]:
            for item in query["relevant"]:
                assert not str(item["source_id"]).startswith("/")
                assert item["grade"] == 1
                assert item["chunk_id"] == chunk_id_for(item["corpus"], item["source_id"], item["part_key"])
                assert item["chunk_id"] in indexed
                if item["corpus"] == "rules":
                    assert item["source_id"] == RULES_SOURCE_ID
                    seen_rules[query["id"]] = item["part_key"]
        assert seen_rules == RULES
