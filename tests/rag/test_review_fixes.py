"""Правки ревью #348: purge, whitelist, профиль, фикстуры."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.llm_fixtures import fixture_key
from app.orchestrator.rag.loaders import (
    load_nodes_and_prompts,
    reset_version_fallback_count,
    version_fallback_count,
)
from app.orchestrator.rag.rrf import rrf_merge
from app.orchestrator.rag.store import RagStore, RagSyncError
from app.orchestrator.rag.types import SourceDoc
from evals.orchestrator import project65
from evals.orchestrator.checks import check_vo_lengths
from evals.orchestrator.e6_dataset import e6_queries
from evals.orchestrator.mock_actions import ALLOWED_ACTIONS, MockActionBackend, rollback_prompt
from evals.orchestrator.precision_live import choose_default


def _docs(count: int) -> list[SourceDoc]:
    return [
        SourceDoc(
            corpus="history",
            source_id=f"task:{index}",
            title=f"Документ {index}",
            text=f"текст источника {index} про кадры",
        )
        for index in range(count)
    ]


def _history_engine(tmp_path: Path, docs: list[SourceDoc]) -> RagEngine:
    return RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"history": docs, "rules": [], "nodes": [], "prompts": []},
        scan_git=False,
    )


def test_sync_blocks_empty_incoming_and_majority_purge(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    empty = RagStore(tmp_path / "empty" / "orch_rag.db")
    assert empty.sync("history", {}, {}, indexed_at="t0") == (0, 0, 0)
    docs = _docs(4)
    engine = _history_engine(tmp_path, docs)
    try:
        engine.reindex("history")
        with caplog.at_level(logging.WARNING), pytest.raises(RagSyncError) as empty_incoming:
            engine.store.sync("history", {}, {}, indexed_at="t1")
        assert empty_incoming.value.source_ids == [f"task:{index}" for index in range(4)]
        assert "task:0" in caplog.text
        engine.static_docs = {"history": docs[:1], "rules": [], "nodes": [], "prompts": []}
        with pytest.raises(RagSyncError) as majority:
            engine.reindex("history")
        assert len(majority.value.source_ids) == 3
        kept = engine.store.existing_revisions("history")
        assert set(kept) == {f"task:{index}" for index in range(4)}
    finally:
        engine.close()


def test_bm25_operational_error_is_not_an_empty_hit(tmp_path: Path) -> None:
    engine = _history_engine(tmp_path, _docs(1))
    try:
        engine.reindex("history")
        assert engine.store.bm25("", ["history"], limit=5) == []
        with pytest.raises(sqlite3.OperationalError):
            engine.store.bm25('"', ["history"], limit=5)
    finally:
        engine.close()


def test_rrf_weights_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        rrf_merge([["a"], ["b"]], weights=[0.0, 1.0])
    engine = _history_engine(tmp_path, _docs(1))
    try:
        engine.reindex("history")
        with pytest.raises(ValueError):
            engine.retrieve("текст", ["history"], rrf_weights=(0.0, 1.5))
    finally:
        engine.close()


def test_allowed_actions_reject_rollback_and_helper_restores() -> None:
    backend = MockActionBackend()
    from app.orchestrator.rag.node_store import ResolvedNode

    original = "исходный промпт\n"
    backend.node_store.upsert(
        ResolvedNode(
            node_key="n_guard",
            group_key="custom",
            title="Страж",
            status="active",
            prompt_template=original,
            source="db",
            version=1,
            db_version=1,
            git_sha256=None,
            stale_base=False,
            params={},
        )
    )
    rejected = backend.execute("rollback", {"node_key": "n_guard", "prompt_template": "чужой\n"})
    assert rejected.ok is False
    assert rejected.data["error"] == "not_in_whitelist"
    assert "rollback" not in ALLOWED_ACTIONS
    assert backend.execute("shell", {}).data["error"] == "not_in_whitelist"
    assert backend.node_store.resolve_node("n_guard").prompt_template == original
    restored = rollback_prompt(backend, "n_guard", "после отката\n")
    assert restored.ok is True
    assert backend.node_store.resolve_node("n_guard").prompt_template == "после отката\n"


def test_edit_node_prompt_replace_full_and_append() -> None:
    from app.orchestrator.rag.node_store import ResolvedNode

    backend = MockActionBackend()
    backend.node_store.upsert(
        ResolvedNode(
            node_key="n_edit",
            group_key="custom",
            title="Правка",
            status="active",
            prompt_template="## INTRO\nстарт\n",
            source="db",
            version=1,
            db_version=1,
            git_sha256=None,
            stale_base=False,
            params={},
        )
    )
    appended = backend.execute(
        "edit_node_prompt",
        {"node_key": "n_edit", "patch_mode": "append_section", "section_anchor": "TAIL", "content": "хвост"},
    )
    assert appended.ok is True
    text = backend.node_store.resolve_node("n_edit").prompt_template
    assert "## INTRO" in text and "## TAIL" in text and "хвост" in text
    replaced = backend.execute(
        "edit_node_prompt",
        {"node_key": "n_edit", "patch_mode": "replace_full", "content": "целиком новый промпт"},
    )
    assert replaced.ok is True
    assert backend.node_store.resolve_node("n_edit").prompt_template.strip() == "целиком новый промпт"


def test_paid_ledger_charges_once() -> None:
    backend = MockActionBackend()
    first = backend.execute("run_node", {"project_id": 65, "node_key": "n_edit"})
    retry = backend.execute("run_node", {"project_id": 65, "node_key": "n_edit"})
    assert first.cost == 1.0
    assert retry.cost == 0.0
    assert backend.spend_usd == 1.0


def test_e6_json_matches_builder() -> None:
    path = Path(project65.DATASETS) / "e6_queries.json"
    assert json.loads(path.read_text(encoding="utf-8")) == e6_queries()


def test_seeded_registry_file_matches_dataset() -> None:
    folder = project65.DATASETS
    registry = json.loads((folder / "65_seeded_registry.json").read_text(encoding="utf-8"))
    seeded = json.loads((folder / "65-seeded.json").read_text(encoding="utf-8"))
    assert registry["registry"] == seeded["registry"]


def test_fixture_key_matches_routing_hashing() -> None:
    hashing = pytest.importorskip("app.orchestrator.routing.hashing")
    messages = [
        {"role": "system", "content": "sys", "extra": "ignored"},
        {"role": "user", "content": "привет"},
    ]
    assert fixture_key("strong", messages, 1.0, 16, "high") == hashing.fixture_key(
        "strong", messages, 1.0, 16, "high"
    )


def test_profile_flag_skips_reranker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.orchestrator.rag as rag_mod
    from app.orchestrator.rag.embedder import build_embedder

    rag_mod.reset_engine()
    monkeypatch.setenv("ORCH_RAG_RERANK", "0")
    monkeypatch.setattr(rag_mod, "default_db_path", lambda: tmp_path / "orch_rag.db")

    def fake_embedder(kind: str):
        assert kind == "e5-large"
        return build_embedder("hashing")

    def boom(*_args, **_kwargs):
        raise AssertionError("реранкер не должен строиться")

    monkeypatch.setattr("app.orchestrator.rag.embedder.build_embedder", fake_embedder)
    monkeypatch.setattr("app.orchestrator.rag.rerank.build_reranker", boom)
    engine = rag_mod.get_engine(profile="contour-b")
    try:
        assert engine.reranker is None
        assert engine.retrieve_defaults["rerank"] is False
        assert engine.retrieve_defaults["rrf_k"] == 20
        assert engine.retrieve_defaults["rrf_weights"] == (0.5, 1.5)
        assert engine.retrieve_defaults["rerank_fusion"] == "rrf"
        assert engine.retrieve_defaults["rerank_fusion_weights"] == (0.25, 1.5)
        assert engine.retrieve_defaults["bm25_protect"] == 0.0
        assert engine.retrieve_defaults["family_boost"] == 1.0
    finally:
        rag_mod.reset_engine()


def test_non_numeric_template_version_defaults(tmp_path: Path) -> None:
    folder = tmp_path / "templates" / "node_groups" / "custom"
    folder.mkdir(parents=True)
    (folder / "n_bad_version.md").write_text(
        "---\nnode_key: n_bad_version\ntitle: Плохая\ngroup_key: custom\nversion: abc\n---\n"
        "тело шаблона badversionbody\n",
        encoding="utf-8",
    )
    reset_version_fallback_count()
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        repo_root=tmp_path,
        scan_git=True,
    )
    try:
        engine.reindex("nodes")
        hits = engine.retrieve("badversionbody", ["nodes"], top_k=4)
    finally:
        engine.close()
    assert version_fallback_count() == 1
    assert hits
    assert hits[0].metadata["version"] == 1


def test_node_load_drops_stale_role_cache(tmp_path: Path) -> None:
    import app.orchestrator.rag.loaders as loaders

    loaders._GROUP_ROLE_CACHE = {"stale": {"group_role": "устарело"}}
    load_nodes_and_prompts(repo_root=tmp_path, node_store=None, scan_git=False)
    assert loaders._GROUP_ROLE_CACHE is None


def test_choose_default_says_none_when_p95_misses() -> None:
    key, why = choose_default(
        [
            ("e5-large+bge-m3+p20+t384", "slow", {"ndcg_at_8": 0.8, "hit_at_5": 0.9, "p95_s": 2.2}),
        ]
    )
    assert key == ""
    assert "нет дефолта" in why.lower() or "Нет дефолта" in why


def test_vo_length_type_error_is_not_stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    package = types.ModuleType("app.orchestrator.validators")
    module = types.ModuleType("app.orchestrator.validators.vo_length")

    def bad(_fragments, _extra):  # noqa: ANN001
        return []

    module.check_vo_lengths = bad
    monkeypatch.setitem(sys.modules, "app.orchestrator.validators", package)
    monkeypatch.setitem(sys.modules, "app.orchestrator.validators.vo_length", module)
    with pytest.raises(RuntimeError, match="контракт"):
        check_vo_lengths([{"id": "a", "text": "x" * 10}])


def test_fake_resolver_skips_missing_node_and_keeps_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import app.orchestrator.rag.node_api as node_api
    from app.orchestrator.rag.node_store import NodeDef, NodeNotFoundError, NodeStoreError, SqliteNodeStore

    db_path = tmp_path / "nodes.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE node_defs (
            node_key TEXT, group_key TEXT, title TEXT, status TEXT,
            active_version INTEGER, updated_change_id TEXT
        )
        """
    )
    keys = ["n_keep", "n_ghost_a", "n_ghost_b", "n_ghost_c"]
    for key in keys:
        conn.execute(
            "INSERT INTO node_defs VALUES (?, 'custom', ?, 'active', NULL, NULL)",
            (key, key),
        )
    conn.commit()
    conn.close()
    hide_ghosts = False

    def bind():
        def resolve(node_key, _conn, *, templates_root=None):
            del templates_root
            if hide_ghosts and node_key != "n_keep":
                raise NodeNotFoundError(f"нода {node_key} не найдена в git")
            return NodeDef(node_key=node_key, prompt_template=f"тело {node_key} markerkeep", source="git")

        return resolve, NodeNotFoundError, NodeStoreError

    monkeypatch.setattr(node_api, "bind_resolve", bind)
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        repo_root=tmp_path,
        node_store=SqliteNodeStore(db_path),
        scan_git=False,
    )
    try:
        first = engine.reindex(["nodes", "prompts"])
        assert first["nodes"].written == 4
        assert first["prompts"].written == 4
        hide_ghosts = True
        with caplog.at_level(logging.WARNING):
            second = engine.reindex(["nodes", "prompts"])
        assert second["nodes"].deleted == 0
        assert second["prompts"].deleted == 0
        assert second["nodes"].skipped_invalid_keys == 3
        assert set(node_api.skipped_node_keys()) == {"n_ghost_a", "n_ghost_b", "n_ghost_c"}
        assert "n_ghost_a" in caplog.text and "пропущена" in caplog.text
        present = {
            row["source_id"]
            for row in engine.store.snapshot()
            if row.get("corpus") in {"nodes", "prompts"}
        }
        assert set(keys) <= present
    finally:
        engine.close()


def test_lookalike_node_not_found_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.orchestrator.rag.node_api as node_api

    class OtherMissing(Exception):
        pass

    class NodeNotFoundError(Exception):
        pass

    def bind():
        def resolve(node_key, _conn, *, templates_root=None):
            del node_key, templates_root
            raise NodeNotFoundError("нода не найдена в git")

        return resolve, OtherMissing, Exception

    monkeypatch.setattr(node_api, "bind_resolve", bind)
    node_api.reset_skipped_nodes()
    assert node_api.resolve_node("n_orphan", None) is None
    assert node_api.skipped_node_keys() == ("n_orphan",)

    def bind_broken():
        def resolve(node_key, _conn, *, templates_root=None):
            del node_key, templates_root
            raise RuntimeError("active_version не может указывать на revert_to_git")

        return resolve, OtherMissing, RuntimeError

    monkeypatch.setattr(node_api, "bind_resolve", bind_broken)
    with pytest.raises(RuntimeError, match="revert_to_git"):
        node_api.resolve_node("n_revert", None)


def test_stub_ws1_override_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    ws1 = tmp_path / "ws1"
    ws1.mkdir()
    (ws1 / "65-clean.json").write_text(json.dumps({"stub": True, "dataset": "65-clean"}), encoding="utf-8")
    monkeypatch.setattr(project65, "WS1_DIR", ws1)
    with caplog.at_level(logging.WARNING):
        data = project65.load_dataset("65-clean")
    assert data.get("stub") is True
    assert "stub=true" in caplog.text
