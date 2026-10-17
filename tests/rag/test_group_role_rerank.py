"""Роль ноды в чанке и опциональный реранкер. Веса не качаются."""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from app.orchestrator.default_graph import SD_FANOUT
from app.orchestrator.rag.embedder import build_embedder, weight_cache_dir
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.loaders import _node_doc, _prompt_docs, group_role_index
from app.orchestrator.rag.node_api import bind_resolve
from app.orchestrator.rag.node_store import (
    NODE_KEY_RE,
    NodeStoreError,
    ResolvedNode,
    SqliteNodeStore,
    get_node,
    resolve_node,
)
from app.orchestrator.rag.rerank import build_reranker
from app.orchestrator.rag.types import SourceDoc
from app.settings import settings


def _git(node_key: str, title: str, description: str) -> ResolvedNode:
    return ResolvedNode(
        node_key=node_key,
        group_key="scene_design_fanout",
        title=title,
        status="active",
        prompt_template="## Кадр\n\nТекст секции камеры и действия различается.\n",
        source="git",
        version=1,
        description=description,
    )


def test_neighbor_scene_design_roles_come_from_group_spec() -> None:
    duties = {agent: descr for agent, _label, descr in SD_FANOUT}
    index = group_role_index()
    camera = index["n_excel_gpt_sd_camera"]
    action = index["n_excel_gpt_sd_action"]
    assert camera["group_role"] == duties["camera"]
    assert action["group_role"] == duties["action"]
    assert camera["group_role"] != action["group_role"]
    assert camera["group_order"] != action["group_order"]
    assert "node_groups.py" in camera["role_source"]
    assert "явного поля role нет" in camera["role_source"]
    node = _git("n_excel_gpt_sd_camera", "GPT: камера", duties["camera"])
    doc = _node_doc(node)
    assert "n_excel_gpt_sd_camera" in doc.text
    assert "GPT: камера" in doc.text
    assert duties["camera"] in doc.text
    assert "позиция" in doc.text
    assert doc.metadata["node_key"] == "n_excel_gpt_sd_camera"
    assert doc.metadata["title"] == "GPT: камера"
    assert doc.metadata["group_role"] == duties["camera"]
    assert doc.metadata["group_order"] == camera["group_order"]
    prompts = _prompt_docs(node)
    assert prompts
    assert duties["camera"] in prompts[0].text
    assert prompts[0].metadata["title"] == "GPT: камера"
    assert prompts[0].metadata["group_role"] == duties["camera"]


def test_unknown_node_does_not_invent_a_role() -> None:
    node = _git("n_not_in_any_group", "Без группы", "")
    assert NODE_KEY_RE.match(node.node_key)
    doc = _node_doc(node)
    assert "n_not_in_any_group" in doc.text
    assert "Без группы" in doc.text
    assert "group_role" not in doc.metadata
    assert doc.metadata["title"] == "Без группы"


class _LastWins:
    reranker_id = "last-wins"

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(index) for index in range(len(passages))]


def test_rerank_flag_is_optional_and_reorders_hybrid(tmp_path: Path) -> None:
    docs = [
        SourceDoc(corpus="nodes", source_id="n_first", title="первый", text="якорь общий первый документ"),
        SourceDoc(corpus="nodes", source_id="n_second", title="второй", text="якорь общий второй документ"),
    ]
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"nodes": docs, "prompts": [], "rules": [], "history": []},
        scan_git=False,
        reranker=_LastWins(),
    )
    try:
        engine.reindex("nodes")
        plain = engine.retrieve("якорь общий", ["nodes"], top_k=2, rerank=False)
        ranked = engine.retrieve("якорь общий", ["nodes"], top_k=2, rerank=True)
        assert len(plain) == 2
        assert ranked[0].chunk_id == plain[-1].chunk_id
        assert ranked[0].chunk_id != plain[0].chunk_id
        assert engine.retrieve("якорь общий", ["nodes"], top_k=2)[0].chunk_id == ranked[0].chunk_id
    finally:
        engine.close()
    assert build_reranker("none") is None
    stub = build_reranker("stub")
    assert stub is not None
    scores = stub.score("план камеры", ["план камеры наезд", "локация мира интерьер"])
    assert scores[0] > scores[1]
    hashing = build_embedder("hashing")
    assert hashing.embedder_id.startswith("e5:hashing")


def test_weight_cache_refuses_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    data = Path(settings.data_dir)
    monkeypatch.setenv("HF_HOME", str(data / "hf-cache"))
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    with pytest.raises(RuntimeError):
        weight_cache_dir()


def test_revert_to_git_pointer_is_treated_as_null(tmp_path: Path) -> None:
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
    conn.execute(
        """
        CREATE TABLE node_prompt_versions (
            node_key TEXT, version INTEGER, prompt_template TEXT, params_json TEXT,
            kind TEXT, change_id TEXT, created_at TEXT, base_git_sha256 TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO node_defs VALUES ('n_revert_guard', 'custom', 'Откат', 'active', 1, 'upd')"
    )
    conn.execute(
        """
        INSERT INTO node_prompt_versions
        VALUES ('n_revert_guard', 1, 'не индексировать', '{}', 'revert_to_git', 'c1',
                '2026-10-04', NULL)
        """
    )
    conn.commit()
    with pytest.raises(NodeStoreError, match="revert_to_git"):
        resolve_node("n_revert_guard", conn, templates_root=tmp_path)
    with pytest.raises(NodeStoreError, match="revert_to_git"):
        get_node("n_revert_guard", conn, templates_root=tmp_path)
    conn.close()
    signature = inspect.signature(resolve_node)
    assert list(signature.parameters) == ["node_key", "conn", "templates_root"]
    assert signature.parameters["templates_root"].kind is inspect.Parameter.KEYWORD_ONLY
    switch = inspect.getsource(bind_resolve)
    assert "from app.orchestrator.node_store import" in switch
    assert "resolve_node" in switch
    store_source = inspect.getsource(SqliteNodeStore.list_overrides)
    assert "_overrides_via_resolve" in store_source
