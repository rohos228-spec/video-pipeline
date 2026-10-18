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


def test_head_bm25_matches_title_key_and_role_not_the_body(tmp_path: Path) -> None:
    docs = [
        SourceDoc(
            corpus="nodes",
            source_id="n_camera",
            title="Камера",
            text="общий текст без искомого слова",
            metadata={"node_key": "n_excel_gpt_sd_camera", "group_role": "постановка кадра"},
        ),
        SourceDoc(
            corpus="nodes",
            source_id="n_world",
            title="Мир",
            text="постановка кадра упомянута только в теле чанка",
            metadata={"node_key": "n_excel_gpt_sd_world", "group_role": "локации и интерьеры"},
        ),
    ]
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"nodes": docs, "prompts": [], "rules": [], "history": []},
        scan_git=False,
    )
    try:
        engine.reindex("nodes")
        head = engine.retrieve("постановка", ["nodes"], top_k=2, channel="bm25", bm25_scope="head")
        full = engine.retrieve("постановка", ["nodes"], top_k=2, channel="bm25", bm25_scope="full")
    finally:
        engine.close()
    assert [chunk.source_id for chunk in head] == ["n_camera"]
    assert [chunk.source_id for chunk in full] == ["n_world"]


def test_contour_b_profile_records_the_measured_default() -> None:
    from app.orchestrator.rag.profile import (
        CONTOUR_B_AGGREGATE,
        CONTOUR_B_BM25_PROTECT,
        CONTOUR_B_BM25_SCOPE,
        CONTOUR_B_FAMILY_BOOST,
        CONTOUR_B_FUSION_BM25,
        CONTOUR_B_FUSION_RERANK,
        CONTOUR_B_RERANK_FUSION,
        CONTOUR_B_EMBEDDER,
        CONTOUR_B_RERANK_MAX_LENGTH,
        CONTOUR_B_RERANK_POOL,
        CONTOUR_B_RERANKER,
        CONTOUR_B_RRF_K,
        CONTOUR_B_RRF_LEXICAL,
        CONTOUR_B_RRF_VECTOR,
    )
    from evals.orchestrator.precision_live import choose_default

    assert CONTOUR_B_EMBEDDER == "e5-large"
    assert CONTOUR_B_RERANKER == "bge-m3"
    assert CONTOUR_B_RRF_K == 20
    assert (CONTOUR_B_RRF_LEXICAL, CONTOUR_B_RRF_VECTOR) == (0.5, 1.5)
    assert CONTOUR_B_RRF_VECTOR > CONTOUR_B_RRF_LEXICAL
    assert CONTOUR_B_BM25_SCOPE == "full"
    assert CONTOUR_B_RERANK_POOL == 12
    assert CONTOUR_B_AGGREGATE == "expand_tail"
    assert CONTOUR_B_RERANK_MAX_LENGTH == 256
    assert CONTOUR_B_RERANK_FUSION == "rrf"
    assert (CONTOUR_B_FUSION_BM25, CONTOUR_B_FUSION_RERANK) == (0.25, 1.5)
    assert CONTOUR_B_BM25_PROTECT == 0.0
    assert CONTOUR_B_FAMILY_BOOST == 1.0
    assert build_reranker("none") is None
    kept = choose_default(
        [
            ("e5-small+k20+w0.5/1.5+full", "small", {"ndcg_at_8": 0.9, "hit_at_5": 0.9, "p95_s": 0.01}),
            ("e5-large+k20+w0.5/1.5+full", "large", {"ndcg_at_8": 0.6, "hit_at_5": 0.8, "p95_s": 0.06}),
            ("e5-large+jina+p10+t256", "jina", {"ndcg_at_8": 0.59, "hit_at_5": 0.9, "p95_s": 0.4}),
        ]
    )
    assert kept[0] == "e5-small+k20+w0.5/1.5+full"
    none = choose_default(
        [("e5-large+bge-m3+p20+t384", "slow", {"ndcg_at_8": 0.8, "hit_at_5": 0.95, "p95_s": 2.2})]
    )
    assert none[0] == ""
    assert "нет дефолта" in none[1].lower() or "Нет дефолта" in none[1]
    led = choose_default(
        [
            ("e5-large+k20+w0.5/1.5+full", "large", {"ndcg_at_8": 0.60, "hit_at_5": 0.88, "p95_s": 0.06}),
            ("e5-large+bge-m3+p20+t256", "m3", {"ndcg_at_8": 0.65, "hit_at_5": 0.96, "p95_s": 1.2}),
            ("e5-large+bge-m3+p20+t384", "slow", {"ndcg_at_8": 0.70, "hit_at_5": 0.98, "p95_s": 1.3}),
        ]
    )
    assert led[0] == "e5-large+bge-m3+p20+t256"
    # Прогон после переноса меток: hit@5 новой разметки 0.82 у обоих, дефолта нет.
    transferred_pair = choose_default(
        [
            (
                "e5-large+k20+w0.5/1.5+full",
                "hybrid",
                {"ndcg_at_8": 0.45738356018163073, "hit_at_5": 0.82, "p95_s": 0.057675631799338586},
            ),
            (
                "e5-large+bge-m3+p20+t256",
                "m3",
                {"ndcg_at_8": 0.45740439515157605, "hit_at_5": 0.82, "p95_s": 1.4471942315503838},
            ),
        ]
    )
    assert transferred_pair[0] == ""
    # Вариант C при p95 1.49 выше лимита 1.2 с: дефолта нет.
    variant_c = choose_default(
        [
            (
                "e5-large+k20+w0.5/1.5+full",
                "hybrid",
                {"ndcg_at_8": 0.606895333872647, "hit_at_5": 0.88, "p95_s": 0.06456130355018104},
            ),
            (
                "e5-large+bge-m3+p20+t256",
                "m3",
                {"ndcg_at_8": 0.6234688527392744, "hit_at_5": 0.94, "p95_s": 1.4905728171985175},
            ),
        ]
    )
    assert variant_c[0] == ""


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
    resolve_fn, _missing, broken = bind_resolve()
    try:
        import importlib

        importlib.import_module("app.orchestrator.node_store")
        assert resolve_fn.__module__.startswith("app.orchestrator.node_store")
    except ImportError:
        assert resolve_fn.__module__ == "app.orchestrator.rag.node_store"
    with pytest.raises(broken, match="revert_to_git"):
        SqliteNodeStore(db_path).list_overrides(templates_root=tmp_path)
