"""Рантайм rag пишет только orch_rag.db (+ wal/shm) и только из reindex."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.rag import reset_engine, retrieve
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.store import RAG_SIDECAR_NAMES, connect
from app.settings import settings


def test_retrieve_does_not_create_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    reset_engine()
    try:
        assert retrieve("любой запрос", ["rules"], top_k=4) == []
        assert list(data.iterdir()) == []
    finally:
        reset_engine()


def test_reindex_writes_only_orch_rag_db(tmp_path: Path) -> None:
    db = tmp_path / "orch_rag.db"
    engine = RagEngine(
        db_path=db,
        rules_path=tmp_path / "missing-rules.md",
        scan_git=False,
    )
    try:
        engine.reindex("rules")
        names = {path.name for path in tmp_path.iterdir() if path.is_file()}
        assert "orch_rag.db" in names
        assert names <= {"orch_rag.db", *RAG_SIDECAR_NAMES}
    finally:
        engine.close()


def test_writer_rejects_other_filenames(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="orch_rag.db"):
        connect(tmp_path / "other.db", readonly=False)
