"""Повторный reindex без изменений не пишет строки и не зовёт эмбеддер."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.embedder import E5PrefixEmbedder, HashingEmbedder
from app.orchestrator.rag.engine import RagEngine


class CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed_documents(texts)


def test_second_reindex_is_idempotent(tmp_path: Path) -> None:
    documents = [
        {
            "source_id": "task:1",
            "title": "Один",
            "text": "стабильный текст для идемпотентности",
            "updated_at": "2026-10-17T00:00:00Z",
            "kind": "task",
        }
    ]
    counter = CountingEmbedder()
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        embedder=E5PrefixEmbedder(counter),
        rules_path=tmp_path / "no-rules.md",
        history_documents=documents,
        scan_git=False,
    )
    try:
        first = engine.reindex("history")
        assert first.written == 1
        assert counter.calls == 1
        snapshot = engine.store.snapshot()
        second = engine.reindex("history")
        assert second.written == 0
        assert second.deleted == 0
        assert second.unchanged == 1
        assert counter.calls == 1
        assert engine.store.snapshot() == snapshot
    finally:
        engine.close()
