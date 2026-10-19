"""Инкрементальный reindex: меняется только источник с новой ревизией."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.orchestrator.rag.engine import RagEngine


def _history_engine(tmp_path: Path, documents: list[dict]) -> RagEngine:
    return RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        history_documents=documents,
        scan_git=False,
    )


def test_history_reindex_touches_only_changed_source(tmp_path: Path) -> None:
    documents = [
        {
            "source_id": "task:alpha",
            "title": "Альфа",
            "text": "первый текст альфы про склейку кадров",
            "project_id": 65,
            "updated_at": "2026-10-01T00:00:00Z",
            "kind": "task",
        },
        {
            "source_id": "task:beta",
            "title": "Бета",
            "text": "второй текст беты про музыку",
            "project_id": 65,
            "updated_at": "2026-10-01T00:00:00Z",
            "kind": "task",
        },
    ]
    engine = _history_engine(tmp_path, documents)
    try:
        first = engine.reindex("history")
        assert first.written == 2
        assert first.unchanged == 0
        before = engine.store.snapshot()
        beta_before = next(row for row in before if row.get("source_id") == "task:beta")
        documents[0]["text"] = "альфа изменилась, бета нет"
        second = engine.reindex("history")
        assert second.written == 1
        assert second.unchanged == 1
        assert second.deleted == 0
        after = engine.store.snapshot()
        beta_after = next(row for row in after if row.get("source_id") == "task:beta")
        assert beta_after == beta_before
        alpha = next(row for row in after if row.get("source_id") == "task:alpha")
        assert "изменилась" in alpha["text"]
    finally:
        engine.close()


def test_project_db_and_chat_windows(tmp_path: Path) -> None:
    db_path = tmp_path / "project.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE projects (id INTEGER, slug TEXT, title TEXT, topic TEXT, status TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO projects VALUES (65, 'tkach-finalochka', 'Ткач', 'финал', 'frames_ready', '2026-10-02')"
    )
    conn.execute("CREATE TABLE orch_messages (id INTEGER, session_id TEXT, role TEXT, content TEXT)")
    for index in range(12):
        conn.execute(
            "INSERT INTO orch_messages VALUES (?, 'sess-1', 'user', ?)",
            (index, f"сообщение {index} про проект ткач"),
        )
    conn.commit()
    conn.close()
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        project_db=db_path,
        scan_git=False,
    )
    try:
        result = engine.reindex("history")
        # проект + два окна чата по 10 и 2 сообщения
        assert result.written == 3
        hits = engine.retrieve("tkach-finalochka", ["history"], top_k=4)
        assert any(hit.source_id == "project:65" for hit in hits)
        assert all(hit.corpus == "history" for hit in hits)
    finally:
        engine.close()


def test_removed_source_is_deleted(tmp_path: Path) -> None:
    documents = [
        {"source_id": "task:keep", "title": "Оставить", "text": "якорь keepsourcealpha", "kind": "task"},
        {"source_id": "task:drop", "title": "Убрать", "text": "якорь dropsourcebeta", "kind": "task"},
    ]
    engine = _history_engine(tmp_path, documents)
    try:
        engine.reindex("history")
        del documents[1]
        result = engine.reindex("history")
        assert result.deleted == 1
        assert result.unchanged == 1
        hits = engine.retrieve("dropsourcebeta", ["history"], top_k=4)
        assert hits == [] or all(hit.source_id != "task:drop" for hit in hits)
    finally:
        engine.close()
