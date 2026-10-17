"""Канонические имена и алиасы плана читают одну и ту же колонку индекса."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import get_args

from app.orchestrator.rag import rag_search
from app.orchestrator.rag.corpora import CANONICAL_CORPORA, CORPUS_ALIASES
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.loaders import load_history
from app.orchestrator.rag.types import CorpusName, SourceDoc


def _marker(corpus: str) -> str:
    return f"{corpus}marker"


def _engine(tmp_path: Path) -> RagEngine:
    docs = {
        corpus: [
            SourceDoc(
                corpus=corpus,
                source_id=f"{corpus}-src",
                title=_marker(corpus),
                text=f"токен {_marker(corpus)} лежит в индексе",
            )
        ]
        for corpus in CANONICAL_CORPORA
    }
    return RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs=docs,
        scan_git=False,
    )


def _stored_corpus(db_path: Path, chunk_id: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT corpus FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return str(row[0])


def test_rag_search_canonical_and_aliases_hit_the_stored_index(tmp_path, monkeypatch) -> None:
    assert get_args(CorpusName) == CANONICAL_CORPORA
    engine = _engine(tmp_path)
    monkeypatch.setattr("app.orchestrator.rag.get_engine", lambda **_kwargs: engine)
    try:
        engine.reindex()
        names = [*CANONICAL_CORPORA, *CORPUS_ALIASES]
        for name in names:
            expected = name if name in CANONICAL_CORPORA else CORPUS_ALIASES[name]
            hits = rag_search(_marker(expected), corpora=[name], top_k=4, channel="bm25")
            assert hits, name
            assert {hit.corpus for hit in hits} == {expected}
            stored = {_stored_corpus(engine.db_path, hit.chunk_id) for hit in hits}
            assert stored == {expected}
            other = rag_search(_marker(expected), corpora=["rules"], top_k=4, channel="bm25")
            if expected != "rules":
                assert other == []
    finally:
        engine.close()


def test_project_rows_and_chats_are_stored_in_history(tmp_path: Path) -> None:
    db_path = tmp_path / "project.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE projects (id INTEGER, slug TEXT, title TEXT, topic TEXT, status TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO projects VALUES (7, 'slugseven', 'Семь', 'тема', 'draft', '2026-10-01')"
    )
    conn.execute(
        "CREATE TABLE orch_messages (id INTEGER, session_id TEXT, role TEXT, content TEXT)"
    )
    conn.execute("INSERT INTO orch_messages VALUES (1, 'sess', 'user', 'реплика чата')")
    conn.commit()
    conn.close()

    docs = load_history(project_db=db_path)
    assert docs
    assert {doc.corpus for doc in docs} == {"history"}
    assert any(doc.metadata.get("kind") == "project" for doc in docs)
    assert any(doc.metadata.get("kind") == "chat" for doc in docs)
