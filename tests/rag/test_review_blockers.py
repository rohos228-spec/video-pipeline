"""Блокеры ревью контура B: детерминизм, три прохода, E8, p95, правило 1."""

from __future__ import annotations

import json
import os
from pathlib import Path

import onnxruntime as ort
import pytest

from app.orchestrator.rag.chunking import split_rules
from app.orchestrator.rag.embedder import OnnxMultilingualE5
from app.orchestrator.rag.onnx_session import (
    EVAL_INTER_OP_THREADS,
    EVAL_INTRA_OP_THREADS,
    EVAL_ONNX_SEED,
)
from app.orchestrator.rag.rerank import OnnxBgeM3Reranker
from app.project_root import find_project_root
from evals.orchestrator.precision_dataset import build_precision_dataset
from evals.orchestrator.precision_live import (
    NDCG_AT_8_MIN,
    P95_MAX_S,
    aggregate_pass_slices,
    choose_default,
    contour_c_acceptance,
    render_locked_report,
    write_pass_artifact,
)
from evals.orchestrator.run_evals import run_contour_a

ROOT = find_project_root()
RULES = ROOT / "docs" / "fable" / "user_rules.md"
RULE_1 = "Сдвиг батчей 2–5 с — только локальный housepc, в main не требуется (решение пользователя 17.10)."


class _FakeTokenizer:
    @staticmethod
    def from_file(path: str) -> _FakeTokenizer:
        return _FakeTokenizer()

    def enable_padding(self, **_kwargs: object) -> None:
        return None

    def enable_truncation(self, **_kwargs: object) -> None:
        return None


class _FakeSession:
    def __init__(self, path: str, sess_options: object = None, providers: object = None) -> None:
        self.path = path
        self.sess_options = sess_options

    def get_inputs(self) -> list[object]:
        return []


def _patch_onnx(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[object],
    seeds: list[int],
    downloads: list[str],
) -> None:
    import huggingface_hub
    import tokenizers

    monkeypatch.setattr(ort, "InferenceSession", _FakeSession)
    monkeypatch.setattr(ort, "set_seed", lambda seed: seeds.append(seed))

    def _download(_repo: str, filename: str, cache_dir: str | None = None) -> str:
        downloads.append(filename)
        return "model.onnx"

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _download)
    monkeypatch.setattr(tokenizers, "Tokenizer", _FakeTokenizer)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)

    real_session = ort.InferenceSession

    def _capture(path: str, sess_options: object = None, providers: object = None) -> _FakeSession:
        captured.append(sess_options)
        return real_session(path, sess_options=sess_options, providers=providers)

    monkeypatch.setattr(ort, "InferenceSession", _capture)


def test_eval_reindex_can_raise_the_worker_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator.rag.engine import RagEngine

    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        scan_git=False,
    )
    seen: dict[str, float] = {}

    def _submit(fn, *, timeout: float = 120):  # noqa: ANN001
        seen["timeout"] = timeout
        return fn()

    monkeypatch.setattr(engine._worker, "submit", _submit)
    try:
        engine.reindex("rules", timeout=900)
    finally:
        engine.close()
    assert seen["timeout"] == 900


def test_eval_mode_sessions_use_one_thread_and_fixed_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []
    seeds: list[int] = []
    downloads: list[str] = []
    _patch_onnx(monkeypatch, captured, seeds, downloads)
    OnnxMultilingualE5("intfloat/multilingual-e5-large", "e5", eval_mode=True)
    OnnxBgeM3Reranker(max_length=64, eval_mode=True)
    assert len(captured) == 2
    for options in captured:
        assert options.intra_op_num_threads == EVAL_INTRA_OP_THREADS == 1
        assert options.inter_op_num_threads == EVAL_INTER_OP_THREADS == 1
        assert options.execution_mode == ort.ExecutionMode.ORT_SEQUENTIAL
        assert options.use_deterministic_compute is True
    assert seeds == [EVAL_ONNX_SEED, EVAL_ONNX_SEED]
    assert "onnx/model.onnx" in downloads
    assert "onnx/model.onnx_data" in downloads
    assert "onnx/model_quantized.onnx" not in downloads


def test_production_sessions_keep_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []
    seeds: list[int] = []
    downloads: list[str] = []
    _patch_onnx(monkeypatch, captured, seeds, downloads)
    OnnxMultilingualE5("intfloat/multilingual-e5-large", "e5", eval_mode=False)
    OnnxBgeM3Reranker(max_length=64, eval_mode=False)
    assert captured[0].intra_op_num_threads == 8
    assert captured[1].intra_op_num_threads == 8
    assert captured[1].inter_op_num_threads == 1
    assert seeds == []
    assert "onnx/model_quantized.onnx" in downloads


def _slice(hit: float, ndcg: float, doc: float = 0.5, p95: float = 0.2, n: int = 4) -> dict[str, float]:
    return {"hit_at_5": hit, "ndcg_at_8": ndcg, "ndcg_doc": doc, "p95_s": p95, "n": n}


def test_three_passes_write_raw_metrics_and_take_median(tmp_path: Path) -> None:
    same = {"dev": _slice(0.92, 0.70), "test": _slice(0.90, 0.66), "all": _slice(0.91, 0.68)}
    packed = aggregate_pass_slices([same, same, same])
    assert packed["pass_count"] == 3
    assert packed["identical"] is True
    assert packed["median"]["test"]["hit_at_5"] == 0.90
    assert packed["median"]["test"]["ndcg_at_8"] == 0.66
    noisy = [
        {"dev": _slice(0.92, 0.10, p95=0.1), "test": _slice(0.80, 0.10, p95=0.4)},
        {"dev": _slice(0.92, 0.30, p95=0.2), "test": _slice(0.80, 0.30, p95=0.1)},
        {"dev": _slice(0.92, 0.20, p95=0.9), "test": _slice(0.80, 0.20, p95=0.2)},
    ]
    mixed = aggregate_pass_slices(noisy)
    assert mixed["identical"] is False
    assert mixed["median"]["dev"]["ndcg_at_8"] == 0.20
    assert mixed["median"]["dev"]["hit_at_5"] == 0.92
    assert mixed["median"]["dev"]["p95_s"] == 0.2
    artifact = tmp_path / "passes.json"
    write_pass_artifact(artifact, mixed)
    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert len(saved["passes"]) == 3
    assert saved["passes"][0]["dev"]["ndcg_at_8"] == 0.10
    assert saved["passes"][1]["dev"]["ndcg_at_8"] == 0.30
    assert saved["passes"][2]["dev"]["ndcg_at_8"] == 0.20


def test_kill_unpaid_within_2s_is_deferred_without_clock_literals() -> None:
    source = (ROOT / "evals" / "orchestrator" / "run_evals.py").read_text(encoding="utf-8")
    assert "t_kill" not in source
    assert "t_cancel" not in source
    report = run_contour_a()
    checks = report["by_id"]["E8"]["details"]["checks"]
    assert "kill_unpaid_within_2s" not in checks
    assert all(checks.values())
    deferred = report["by_id"]["E8"]["details"]["deferred_checks"]["kill_unpaid_within_2s"]
    assert deferred["passed"] is None
    assert deferred["status"] == "deferred"
    assert report["e8_passed"] is True


def test_choose_default_p95_limit_is_1_2() -> None:
    assert P95_MAX_S == 1.2
    over, why_over = choose_default(
        [("slow", "slow", {"ndcg_at_8": 0.9, "hit_at_5": 0.95, "p95_s": 1.3})]
    )
    assert over == ""
    assert "1.2" in why_over
    under, why_under = choose_default(
        [("fit", "fit", {"ndcg_at_8": 0.7, "hit_at_5": 0.91, "p95_s": 1.2})]
    )
    assert under == "fit"
    assert "1.2" in why_under


def test_precision_at_8_threshold_phrase_is_gone() -> None:
    banned = "порог precision@8"
    paths = [
        ROOT / "evals" / "orchestrator" / "precision_live.py",
        ROOT / "evals" / "orchestrator" / "precision_dataset.py",
        ROOT / "evals" / "orchestrator" / "datasets" / "precision_at_8.json",
        ROOT / "evals" / "orchestrator" / "datasets" / "precision_at_8_report.md",
        ROOT / "tests" / "rag" / "test_precision_at_8.py",
    ]
    for path in paths:
        assert banned not in path.read_text(encoding="utf-8").lower()


def test_rule_1_text_replaced_and_labels_kept() -> None:
    text = RULES.read_text(encoding="utf-8")
    assert RULE_1 in text
    assert "stagger_sec" not in text
    assert "[2, 5]" not in text
    rule_1 = next(doc for doc in split_rules(text, source_id="user_rules.md") if doc.part_key == "rule-1")
    assert RULE_1 in rule_1.text
    queries = json.loads(
        (ROOT / "evals" / "orchestrator" / "datasets" / "precision_live_queries.json").read_text(encoding="utf-8")
    )
    live = next(query for query in queries if any(item.get("part_key") == "rule-1" for item in query["relevant"]))
    assert live["text"].startswith("На сколько разносить")
    labels = json.loads(
        (ROOT / "evals" / "orchestrator" / "datasets" / "precision_live_labels_s0_s7.json").read_text(
            encoding="utf-8"
        )
    )
    labeled = next(query for query in labels["queries"] if query["id"] == live["id"])
    assert any(item.get("part_key") == "rule-1" for item in labeled["relevant"])
    dataset = build_precision_dataset()
    stagger_docs = [doc for doc in dataset["documents"] if doc["source_id"].startswith("stagger-")]
    assert len(stagger_docs) == 8
    for doc in stagger_docs:
        assert RULE_1 in doc["text"]
        assert "stagger_sec" not in doc["text"]
        assert "staggersec4rule" in doc["text"]


def test_locked_remeasure_passes_match_and_r5_is_marked() -> None:
    from app.orchestrator.rag.profile import CONTOUR_B_EXPERIMENTAL, CONTOUR_B_R5_NOTE

    payload = json.loads(
        (ROOT / "evals" / "orchestrator" / "datasets" / "precision_live_passes.json").read_text(encoding="utf-8")
    )
    assert payload["identical"] is True
    assert payload["pass_count"] == 3
    assert payload["acceptance"]["threshold"] == 0.65
    assert payload["median"]["test"]["ndcg_at_8"] >= 0.65
    assert payload["acceptance"]["experimental"] is False
    assert payload["acceptance"]["mark"] == ""
    assert payload["median"]["test"]["hit_at_5"] < 0.9
    assert payload["prod_p95"]["median_s"] <= P95_MAX_S
    assert str(payload["prod_p95"]["commit"]).startswith("fadcb6cf")
    assert len({item["pid"] for item in payload["pass_meta"]}) == 3
    for name in payload["passes"][0]:
        hits = [item[name]["hit_at_5"] for item in payload["passes"]]
        ndcgs = [item[name]["ndcg_at_8"] for item in payload["passes"]]
        assert hits[0] == hits[1] == hits[2]
        assert ndcgs[0] == ndcgs[1] == ndcgs[2]
    assert CONTOUR_B_EXPERIMENTAL is False
    assert CONTOUR_B_R5_NOTE == ""
    assert NDCG_AT_8_MIN == 0.65


def test_dev70_choice_clears_the_experimental_flag() -> None:
    """choose_default на dev-70 выбрал expand_tail + слияние 0.25/1.5, флаг снят."""
    from app.orchestrator.rag.profile import (
        CONTOUR_B_AGGREGATE,
        CONTOUR_B_EXPERIMENTAL,
        CONTOUR_B_FUSION_BM25,
        CONTOUR_B_FUSION_RERANK,
        CONTOUR_B_RERANK_FUSION,
        CONTOUR_B_RERANK_POOL,
    )
    from evals.orchestrator.dev70_grid import GRID_PATH, grid_runs_for_choice

    assert CONTOUR_B_RERANK_FUSION == "rrf"
    assert CONTOUR_B_FUSION_BM25 == 0.25
    assert CONTOUR_B_FUSION_RERANK == 1.5
    assert CONTOUR_B_AGGREGATE == "expand_tail"
    assert CONTOUR_B_RERANK_POOL == 12
    assert CONTOUR_B_EXPERIMENTAL is False
    missed = choose_default([("rrf", "rrf 0.5/1.5", {"ndcg_at_8": 0.7, "hit_at_5": 0.88, "p95_s": 0.4})])
    assert missed[0] == ""
    document = json.loads(GRID_PATH.read_text(encoding="utf-8"))
    samples = [float(sample) for sample in document["prod_p95_s"]]
    assert len(samples) == 3
    assert max(samples) <= 1.2
    chosen, _why = choose_default(grid_runs_for_choice(document["configs"], prod_p95_s=max(samples)))
    assert chosen == "expand-tail-rrf-0.25-1.5-k20"


def test_r5_below_0_65_is_experimental_and_threshold_stays() -> None:
    assert NDCG_AT_8_MIN == 0.65
    missed = contour_c_acceptance(0.649)
    assert missed["experimental"] is True
    assert missed["mark"] == "R5 не достигнут"
    assert missed["threshold"] == 0.65
    met = contour_c_acceptance(0.65)
    assert met["experimental"] is False
    assert met["mark"] == ""
    report = render_locked_report(
        {
            "identical": True,
            "passes": [],
            "median": {
                "dev": _slice(0.9, 0.7),
                "test": _slice(0.9, 0.64),
                "all": _slice(0.9, 0.67),
            },
            "acceptance": missed,
        }
    )
    assert "R5 не достигнут" in report
    assert "одинаковые hit@5 и nDCG@8: **да**" in report
