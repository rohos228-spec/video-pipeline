"""Сетка dev-70 не открывает holdout-3 и не выбирает просадку hit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.orchestrator.dev70_grid import (
    GRID,
    RERANK_POOL,
    channel_ahead,
    explain_displacement,
    grid_runs_for_choice,
    load_dev70,
)
from evals.orchestrator.holdout import HoldoutLeakError
from evals.orchestrator.precision_live import choose_default


def test_dev70_loader_rejects_holdout3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "evals.orchestrator.dev70_grid.load_dev_expanded",
        lambda: [{"id": "h3-01", "family": "camera", "text": "x", "relevant": []}],
    )
    with pytest.raises(HoldoutLeakError):
        load_dev70()


def test_rerank_grid_is_top_12_only() -> None:
    assert RERANK_POOL == 12
    assert GRID
    assert all(spec.get("rerank_pool", RERANK_POOL) == 12 for spec in GRID)
    assert any(spec["aggregate"] == "prompt_pool" for spec in GRID)
    assert any(spec["rerank"] is False for spec in GRID)


def test_displacement_names_the_other_family_and_the_channel() -> None:
    top = [
        {
            "relevant": False,
            "source_id": "n_excel_gpt_sd_action",
            "part_key": "s0",
            "corpus": "prompts",
            "title": "действие",
            "score_bm25": 3.0,
            "score_vec": 0.2,
        },
        {
            "relevant": True,
            "source_id": "n_excel_gpt_sd_camera",
            "part_key": "catalog",
            "corpus": "nodes",
            "title": "камера",
            "score_bm25": 0.4,
            "score_vec": 0.8,
        },
    ]
    explained = explain_displacement("camera", top)
    assert explained["first_relevant_rank"] == 2
    assert explained["ahead"][0]["owner"] == "action"
    assert explained["ahead"][0]["channel"] == "bm25"
    assert channel_ahead({"score_bm25": 0.1, "score_vec": 0.1}, {"score_bm25": 0.4, "score_vec": 0.8}) == (
        "fusion_or_rerank"
    )


def test_choose_default_drops_prompt_pool_and_keeps_the_higher_ndcg() -> None:
    rows = [
        {"key": "prompt-pool-rrf-0.5-1.5", "rerank": True, "hit_at_5": 0.84, "ndcg_at_8": 0.75},
        {"key": "none-rrf-0.5-1.5", "rerank": True, "hit_at_5": 0.94, "ndcg_at_8": 0.61},
        {"key": "prompt-expand-rrf-0.5-1.5", "rerank": True, "hit_at_5": 0.91, "ndcg_at_8": 0.66},
    ]
    runs = grid_runs_for_choice(rows, prod_p95_s=0.95)
    chosen, _why = choose_default(runs)
    assert chosen == "prompt-expand-rrf-0.5-1.5"
    blocked = grid_runs_for_choice(rows, prod_p95_s=1.21)
    none = [row for row in blocked if row[0] == "none-no-rerank"]
    assert none == []
    chosen_slow, _why = choose_default(blocked)
    assert chosen_slow == ""


def test_focus_diagnosis_is_dev70_and_names_the_displacing_chunks() -> None:
    path = Path("evals/orchestrator/datasets/precision_live_dev70_diagnosis.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["config"] == "none-rrf-0.5-1.5"
    queries = document["queries"]
    assert [query["family"] for query in queries].count("camera") == 8
    assert [query["family"] for query in queries].count("action") == 8
    assert [query["family"] for query in queries].count("script") == 7
    assert all(not str(query["id"]).startswith("h3-") for query in queries)
    missed = next(query for query in queries if query["id"] == "hold-12")
    assert missed["hit5"] is False
    assert missed["first_relevant_rank"] is None
    assert missed["ahead"][0]["owner"] == "action"
    note = Path("docs/eval-dev70-camera-action-script.md").read_text(encoding="utf-8")
    assert "hold-12" in note
    assert "camera" in note and "action" in note and "script" in note
    assert "prompt_pool" in note
