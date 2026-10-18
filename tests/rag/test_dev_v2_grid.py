"""Сетка dev-v2 не читает holdout и отбирает по hit@5, затем nDCG@8."""

from __future__ import annotations

from pathlib import Path

from evals.orchestrator.dev70_grid import GRID as DEV70_GRID
from evals.orchestrator.dev_v2_grid import EXTRAS, GRID, choose_dev_v2, load_dev_v2, pending_specs
from evals.orchestrator.holdout import dev_plus_ids


def test_grid_keeps_dev70_and_adds_cached_knobs() -> None:
    keys = [spec["key"] for spec in GRID]
    assert len(keys) == len(set(keys))
    assert [spec["key"] for spec in DEV70_GRID] == [spec["key"] for spec in GRID[: len(DEV70_GRID)]]
    extra_keys = {spec["key"] for spec in EXTRAS}
    assert extra_keys <= set(keys)
    assert any(spec.get("family_boost") == 2.0 for spec in EXTRAS)
    assert any(spec.get("bm25_protect") == 2.0 for spec in EXTRAS)
    done = {spec["key"] for spec in DEV70_GRID}
    assert [spec["key"] for spec in pending_specs(done)] == [spec["key"] for spec in EXTRAS]
    source = Path("evals/orchestrator/dev_v2_grid.py").read_text(encoding="utf-8")
    assert "holdout5" not in source
    assert "holdout4" not in source
    assert "load_holdout" not in source
    assert "precision_live_holdout" not in source


def test_choose_requires_hit_then_max_ndcg_and_ignores_p95() -> None:
    rows = [
        {"key": "slow", "hit_at_5": 0.91, "ndcg_at_8": 0.80, "p95_fp32_s": 11.0, "dev70_ndcg_at_8": 0.1},
        {"key": "pretty-but-miss", "hit_at_5": 0.89, "ndcg_at_8": 0.99, "p95_fp32_s": 0.2, "dev70_ndcg_at_8": 0.99},
        {"key": "expand-tail-rrf-0.25-1.5-k20", "hit_at_5": 0.93, "ndcg_at_8": 0.70, "p95_fp32_s": 0.4, "dev70_ndcg_at_8": 0.70},
        {"key": "dev70-better", "hit_at_5": 0.90, "ndcg_at_8": 0.66, "p95_fp32_s": 0.3, "dev70_ndcg_at_8": 0.95},
    ]
    assert choose_dev_v2(rows)["key"] == "slow"
    tied = [
        {"key": "expand-tail-rrf-0.25-1.5-k20", "hit_at_5": 0.91, "ndcg_at_8": 0.80},
        {"key": "other", "hit_at_5": 0.91, "ndcg_at_8": 0.80},
    ]
    assert choose_dev_v2(tied)["key"] == "expand-tail-rrf-0.25-1.5-k20"
    assert choose_dev_v2([{"key": "no", "hit_at_5": 0.5, "ndcg_at_8": 0.9}]) is None


def test_dev_v2_loader_is_the_hundred_and_not_holdout_ids() -> None:
    queries = load_dev_v2()
    assert [query["id"] for query in queries[:70]] == dev_plus_ids()
    assert [query["id"] for query in queries[70:]] == [f"d2-{number:02d}" for number in range(1, 31)]
