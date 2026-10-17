"""Неизвестное имя корпуса — ошибка, а не пустой список чанков."""

from __future__ import annotations

import pytest

from app.orchestrator.rag import rag_search
from app.orchestrator.rag.corpora import UnknownCorpus
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import SourceDoc


def test_unknown_corpus_raises_instead_of_empty_hits(tmp_path, monkeypatch) -> None:
    docs = [
        SourceDoc(
            corpus="history",
            source_id="history-src",
            title="historymarker",
            text="токен historymarker лежит в индексе",
        )
    ]
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"history": docs, "rules": [], "nodes": [], "prompts": []},
        scan_git=False,
    )
    monkeypatch.setattr("app.orchestrator.rag.get_engine", lambda **_kwargs: engine)
    try:
        engine.reindex("history")
        with pytest.raises(UnknownCorpus) as caught:
            rag_search("historymarker", corpora=["archive"], top_k=4, channel="bm25")
        assert caught.value.names == ["archive"]
        with pytest.raises(UnknownCorpus) as mixed:
            rag_search("historymarker", corpora=["history", "archive"], top_k=4, channel="bm25")
        assert mixed.value.names == ["archive"]
        hits = rag_search("historymarker", corpora=["history"], top_k=4, channel="bm25")
        assert [hit.corpus for hit in hits] == ["history"]
    finally:
        engine.close()
