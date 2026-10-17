"""Разрешённая версия ноды и git-шаблон. Смена version/change_id переиндексирует промпт."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.orchestrator.rag.canon import sha256_text
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.node_store import MemoryNodeStore, ResolvedNode, SqliteNodeStore


def _node(text: str, *, version: int, change_id: str, status: str = "active") -> ResolvedNode:
    return ResolvedNode(
        node_key="n_qc_vo_length",
        group_key="script_frames_qc",
        title="QC длин",
        status=status,
        prompt_template=text,
        source="db",
        version=version,
        db_version=version,
        git_sha256=None,
        stale_base=False,
        params={"model": "gpt-5.6-sol", "reasoning_effort": "high"},
        change_id=change_id,
        updated_at="2026-10-17T00:00:00Z",
    )


def test_resolved_version_change_reindexes_prompt_only(tmp_path: Path) -> None:
    stable = ResolvedNode(
        node_key="n_stable_node",
        group_key="script_frames_qc",
        title="Стабильная",
        status="active",
        prompt_template="стабильный промпт не менять",
        source="db",
        version=1,
        db_version=1,
        change_id="c-stable",
    )
    store = MemoryNodeStore([_node("версия один", version=1, change_id="c1"), stable])
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        node_store=store,
        scan_git=False,
    )
    try:
        first = engine.reindex("prompts")
        assert first.written == 2
        before = engine.store.snapshot()
        stable_before = next(row for row in before if row.get("source_id") == "n_stable_node")
        store.upsert(_node("версия два якорь", version=2, change_id="c2"))
        second = engine.reindex("prompts")
        assert second.written == 1
        assert second.unchanged == 1
        after = engine.store.snapshot()
        stable_after = next(row for row in after if row.get("source_id") == "n_stable_node")
        assert stable_after == stable_before
        assert any("версия два" in row.get("text", "") for row in after if isinstance(row.get("text"), str))
        store.upsert(_node("версия два якорь", version=2, change_id="c2", status="disabled"))
        third = engine.reindex("prompts")
        assert third.deleted == 1
        hits = engine.retrieve("версия два якорь", ["prompts"], top_k=4)
        assert all(hit.metadata.get("node_key") != "n_qc_vo_length" for hit in hits)
        kept_hits = engine.retrieve("стабильный промпт не менять", ["prompts"], top_k=4)
        kept = next(hit for hit in kept_hits if hit.metadata.get("node_key") == "n_stable_node")
        assert kept.metadata["source"] == "db"
        assert kept.metadata["version"] == 1
        assert kept.metadata["db_version"] == 1
        assert kept.metadata["stale_base"] is False
        assert "active_version" not in kept.metadata
        assert "overridden_in_db" not in kept.metadata
    finally:
        engine.close()


def test_sqlite_node_store_reads_active_row(tmp_path: Path) -> None:
    db_path = tmp_path / "nodes.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE node_defs (
            node_key TEXT, group_key TEXT, title TEXT, origin TEXT,
            status TEXT, active_version INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE node_prompt_versions (
            node_key TEXT, version INTEGER, prompt_template TEXT, params_json TEXT,
            kind TEXT, is_active INTEGER, change_id TEXT, created_at TEXT
        )
        """
    )
    conn.execute("INSERT INTO node_defs VALUES ('n_from_db', 'custom', 'Из БД', 'db', 'active', 2)")
    conn.execute(
        "INSERT INTO node_prompt_versions VALUES (?, 1, 'старая', '{}', 'create', 0, 'c1', '2026-10-01')",
        ("n_from_db",),
    )
    conn.execute(
        """
        INSERT INTO node_prompt_versions
        VALUES ('n_from_db', 2, 'активный промпт dbactiveprompt', '{"reasoning_effort":"high"}',
                'edit_prompt', 1, 'c2', '2026-10-02')
        """
    )
    conn.commit()
    conn.close()
    store = SqliteNodeStore(db_path)
    resolved = store.resolve_node("n_from_db")
    assert resolved is not None
    assert resolved.version == 2
    assert resolved.db_version == 2
    assert resolved.source == "db"
    assert resolved.stale_base is False
    assert not hasattr(resolved, "active_version")
    assert resolved.change_id == "c2"
    assert "dbactiveprompt" in resolved.prompt_template
    assert resolved.params["reasoning_effort"] == "high"
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        node_store=store,
        scan_git=False,
    )
    try:
        engine.reindex("prompts")
        hits = engine.retrieve("dbactiveprompt", ["prompts"], top_k=3)
        assert hits and hits[0].metadata["node_key"] == "n_from_db"
        assert hits[0].metadata["version"] == 2
        assert hits[0].metadata["db_version"] == 2
        assert hits[0].metadata["source"] == "db"
        assert hits[0].metadata["git_sha256"] is None
        assert hits[0].metadata["stale_base"] is False
        assert "active_version" not in hits[0].metadata
        assert hits[0].chunk_id
    finally:
        engine.close()


def test_template_node_group_is_indexed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    folder = repo / "templates" / "node_groups" / "custom"
    folder.mkdir(parents=True)
    (folder / "n_custom_qc.md").write_text(
        "---\nnode_key: n_custom_qc\ntitle: Кастом QC\ngroup_key: custom\n---\n"
        "Якорь customqcanchor в промпте шаблона.\n",
        encoding="utf-8",
    )
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        repo_root=repo,
        scan_git=True,
    )
    try:
        nodes = engine.reindex("nodes")
        prompts = engine.reindex("prompts")
        assert nodes.written >= 1
        assert prompts.written >= 1
        hits = engine.retrieve("customqcanchor", ["nodes", "prompts"], top_k=4)
        matched = [hit for hit in hits if hit.metadata.get("node_key") == "n_custom_qc"]
        assert matched
        assert matched[0].metadata["source"] == "git"
        assert matched[0].metadata["db_version"] is None
        assert matched[0].metadata["stale_base"] is False
        assert matched[0].metadata["git_sha256"]
    finally:
        engine.close()


def test_db_override_marks_stale_base_when_git_hash_differs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    folder = repo / "templates" / "node_groups" / "custom"
    folder.mkdir(parents=True)
    (folder / "n_stale_qc.md").write_text(
        "---\nnode_key: n_stale_qc\ntitle: Stale QC\ngroup_key: custom\n---\nGITPROMPT currentgitbody\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "nodes.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE node_defs (
            node_key TEXT, group_key TEXT, title TEXT, origin TEXT,
            status TEXT, active_version INTEGER, git_sha256 TEXT, stale_base INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE node_prompt_versions (
            node_key TEXT, version INTEGER, prompt_template TEXT, params_json TEXT,
            kind TEXT, is_active INTEGER, change_id TEXT, created_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO node_defs VALUES ('n_stale_qc', 'custom', 'Stale QC', 'git_override', 'active', 2, ?, 0)",
        (sha256_text("OLD PROMPT"),),
    )
    conn.execute(
        """
        INSERT INTO node_prompt_versions
        VALUES ('n_stale_qc', 2, 'DBPROMPT resolveddbbody', '{}', 'edit_prompt', 1, 'c9', '2026-10-02')
        """
    )
    conn.commit()
    conn.close()
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        repo_root=repo,
        node_store=SqliteNodeStore(db_path),
        scan_git=True,
    )
    try:
        engine.reindex("prompts")
        hits = engine.retrieve("resolveddbbody", ["prompts"], top_k=4)
        hit = next(item for item in hits if item.metadata.get("node_key") == "n_stale_qc")
        assert "DBPROMPT" in hit.text
        assert hit.metadata["source"] == "db"
        assert hit.metadata["version"] == 2
        assert hit.metadata["db_version"] == 2
        assert hit.metadata["stale_base"] is True
        assert hit.metadata["git_sha256"] == sha256_text("GITPROMPT currentgitbody\n")
        assert "active_version" not in hit.metadata
        assert "overridden_in_db" not in hit.metadata
    finally:
        engine.close()
