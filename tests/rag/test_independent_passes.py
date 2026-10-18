"""Проходы перемера не делят процесс, базу и кэш."""

from __future__ import annotations

import os

import pytest

from app.orchestrator.rag.embedder import OnnxMultilingualE5
from app.orchestrator.rag.rerank import OnnxBgeM3Reranker
from evals.orchestrator.precision_live import aggregate_pass_slices, run_remeasure


def _slice(hit: float, ndcg: float, doc: float = 0.5, p95: float = 0.2, n: int = 4) -> dict[str, float]:
    return {"hit_at_5": hit, "ndcg_at_8": ndcg, "ndcg_doc": doc, "p95_s": p95, "n": n}


def test_pass_aggregate_records_min_max_and_median() -> None:
    noisy = [
        {"dev": _slice(0.92, 0.10, p95=0.1), "test": _slice(0.80, 0.10, p95=0.4)},
        {"dev": _slice(0.92, 0.30, p95=0.2), "test": _slice(0.80, 0.30, p95=0.1)},
        {"dev": _slice(0.92, 0.20, p95=0.9), "test": _slice(0.80, 0.20, p95=0.2)},
    ]
    packed = aggregate_pass_slices(noisy)
    assert packed["spread"]["dev"]["ndcg_at_8"] == {"min": 0.10, "max": 0.30, "median": 0.20}
    assert packed["spread"]["test"]["p95_s"]["min"] == 0.1
    assert packed["spread"]["test"]["p95_s"]["max"] == 0.4


def test_model_id_names_graph_and_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import huggingface_hub
    import onnxruntime as ort
    import tokenizers

    class _Tokenizer:
        @staticmethod
        def from_file(_path: str) -> _Tokenizer:
            return _Tokenizer()

        def enable_padding(self, **_kwargs: object) -> None:
            return None

        def enable_truncation(self, **_kwargs: object) -> None:
            return None

    class _Session:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def get_inputs(self) -> list[object]:
            return []

    monkeypatch.setattr(ort, "InferenceSession", _Session)
    monkeypatch.setattr(ort, "set_seed", lambda _seed: None)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda *_a, **_k: "model.onnx")
    monkeypatch.setattr(tokenizers, "Tokenizer", _Tokenizer)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)

    eval_embedder = OnnxMultilingualE5("intfloat/multilingual-e5-large", "e5-large", eval_mode=True)
    prod_embedder = OnnxMultilingualE5("intfloat/multilingual-e5-large", "e5-large", eval_mode=False)
    eval_reranker = OnnxBgeM3Reranker(max_length=64, eval_mode=True)
    prod_reranker = OnnxBgeM3Reranker(max_length=64, eval_mode=False)
    assert eval_embedder.embedder_id == "e5-large:fp32:eval:intra1"
    assert prod_embedder.embedder_id == "e5-large:fp32:prod:intra8"
    assert eval_reranker.reranker_id == "onnx-bge-reranker-v2-m3:fp32:eval:intra1"
    assert prod_reranker.reranker_id == "onnx-bge-reranker-v2-m3:q8:prod:intra8"
    assert eval_embedder.embedder_id != prod_embedder.embedder_id
    assert eval_reranker.reranker_id != prod_reranker.reranker_id


def test_remeasure_uses_three_processes_and_three_databases(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_RAG_REMEASURE_EMBEDDER", "hashing")
    artifact = tmp_path / "passes.json"
    report = tmp_path / "report.md"
    payload = run_remeasure(report, artifact)
    metas = payload["pass_meta"]
    assert len(metas) == 3
    assert len({item["pid"] for item in metas}) == 3
    assert len({item["db_path"] for item in metas}) == 3
    assert all(item["db_path"].endswith("orch_rag.db") for item in metas)
    assert all(item["embedder_id"].startswith("e5:hashing") for item in metas)
    assert "spread" in payload
