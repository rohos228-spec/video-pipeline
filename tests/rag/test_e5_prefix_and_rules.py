"""Префиксы e5 и полная инъекция правил, не через top-k."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.embedder import E5PrefixEmbedder, HashingEmbedder
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import SourceDoc
from app.project_root import find_project_root

RULES = find_project_root() / "docs" / "fable" / "user_rules.md"


class SpyEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return super().embed_documents(texts)


def test_query_and_passage_prefixes(tmp_path: Path) -> None:
    spy = SpyEmbedder()
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        embedder=E5PrefixEmbedder(spy),
        rules_path=tmp_path / "no-rules.md",
        scan_git=False,
        static_docs={
            "history": [
                SourceDoc(
                    corpus="history",
                    source_id="task:prefix",
                    title="Префикс",
                    text="текст пассажа для проверки префикса",
                )
            ]
        },
    )
    try:
        engine.reindex("history")
        engine.retrieve("текст запроса", ["history"], top_k=1)
    finally:
        engine.close()
    assert any(item.startswith("passage: ") for item in spy.seen)
    assert any(item.startswith("query: ") for item in spy.seen)


def test_all_twelve_rules_are_injected_and_retrievable(tmp_path: Path) -> None:
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=RULES,
        scan_git=False,
    )
    try:
        block = engine.rules_system_block()
        for number in range(1, 13):
            assert f"## Правило {number}." in block
        result = engine.reindex("rules")
        assert result.written >= 12
        hits = engine.retrieve("reasoning_effort high ноды пайплайна", ["rules"], top_k=3)
        assert any("Правило 2" in hit.title for hit in hits)
    finally:
        engine.close()
