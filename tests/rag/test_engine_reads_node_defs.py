"""get_engine индексирует ноду из node_defs проектной базы."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.orchestrator.rag.node_store import SqliteNodeStore


def _seed(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE node_defs (
            node_key TEXT, group_key TEXT, title TEXT, origin TEXT,
            status TEXT, active_version INTEGER, updated_change_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE node_prompt_versions (
            node_key TEXT, version INTEGER, prompt_template TEXT, params_json TEXT,
            kind TEXT, change_id TEXT, created_at TEXT, base_git_sha256 TEXT,
            created_via TEXT
        )
        """
    )
    conn.execute("INSERT INTO node_defs VALUES ('n_from_db', 'custom', 'Из БД', 'db', 'active', 1, 'upd')")
    conn.execute(
        """
        INSERT INTO node_prompt_versions
        VALUES ('n_from_db', 1, 'активный промпт dbactiveprompt', '{"reasoning_effort":"high"}',
                'create', 'c1', '2026-10-01', NULL, 'orchestrator')
        """
    )
    conn.commit()
    conn.close()


def test_get_engine_finds_a_node_from_node_defs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.orchestrator.rag as rag_mod

    db_path = tmp_path / "state.db"
    _seed(db_path)
    monkeypatch.setattr("app.settings.settings.sqlite_path", db_path)
    monkeypatch.setattr(rag_mod, "default_db_path", lambda: tmp_path / "orch_rag.db")
    rag_mod.reset_engine()
    engine = rag_mod.get_engine()
    try:
        assert isinstance(engine.node_store, SqliteNodeStore)
        assert engine.project_db == db_path
        engine.reindex(["nodes", "prompts"])
        hits = engine.retrieve("dbactiveprompt", ["prompts", "nodes"], top_k=8, rerank=False)
    finally:
        rag_mod.reset_engine()
    assert any(chunk.metadata.get("node_key") == "n_from_db" for chunk in hits)
