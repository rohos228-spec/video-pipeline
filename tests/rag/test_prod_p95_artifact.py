"""p95 прод-пути берётся из замера, не из литерала в генераторе отчёта."""

from __future__ import annotations

import inspect

import pytest

from evals.orchestrator.precision_live import record_prod_p95, render_locked_report


def _payload(**extra: object) -> dict:
    missed = {
        "experimental": True,
        "mark": "R5 не достигнут",
        "threshold": 0.65,
        "test_ndcg_at_8": 0.64,
    }
    row = {"hit_at_5": 0.9, "ndcg_at_8": 0.64, "ndcg_doc": 0.5, "p95_s": 1.0, "n": 4}
    body = {
        "identical": True,
        "passes": [],
        "median": {"dev": row, "test": row, "all": row},
        "acceptance": missed,
    }
    body.update(extra)
    return body


def test_renderer_does_not_embed_old_p95_literals() -> None:
    source = inspect.getsource(render_locked_report)
    assert "0.942" not in source
    assert "0.907" not in source
    empty = render_locked_report(_payload())
    assert "0.942" not in empty
    assert "0.907" not in empty
    assert "в артефакте нет" in empty


def test_renderer_prints_the_measured_median() -> None:
    recorded = record_prod_p95(
        [0.5, 0.9, 0.7],
        config={"reranker": "bge-m3"},
        commit="abc123",
        hardware={"cpu_count": 4},
    )
    assert recorded["median_s"] == 0.7
    assert recorded["cold_query_vectors"] is True
    text = render_locked_report(_payload(prod_p95=recorded))
    assert "0.700" in text
    assert "abc123" in text
    assert "в артефакте нет" not in text


def test_record_prod_p95_rejects_a_missing_measurement() -> None:
    with pytest.raises(ValueError):
        record_prod_p95([], config={}, commit="abc", hardware={})
