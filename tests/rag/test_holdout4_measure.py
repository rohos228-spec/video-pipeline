"""Один замер holdout-4 привязан к набору e344be2e и конфигу 1627ff1e. Повтор запрещён."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.orchestrator.frozen_datasets import FROZEN_SHA256, file_sha256
from evals.orchestrator.precision_live import measure_holdout4_once

DATA = Path("evals/orchestrator/datasets")
MEASURE = "precision_live_holdout4_measure.json"


def test_holdout4_measure_pin_cites_dataset_and_config() -> None:
    path = DATA / MEASURE
    pin = FROZEN_SHA256[MEASURE]
    assert pin["commit"] == "e344be2e"
    assert pin["config_commit"] == "1627ff1e"
    assert file_sha256(path) == pin["sha256"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["dataset_commit"] == "e344be2e68c3652bdc871b3d5094fb95a0c22fbe"
    assert document["config_commit"] == "1627ff1edd9fa4ca87f807dd757d7bc37e2372aa"
    assert document["query_sha256"] == FROZEN_SHA256["precision_live_holdout4.json"]["sha256"]
    assert document["passes"] == 1
    assert document["config"]["aggregate"] == "expand_tail"
    assert document["config"]["rrf_k"] == 20
    assert (document["config"]["fusion_bm25"], document["config"]["fusion_rerank"]) == (0.25, 1.5)
    assert document["thresholds"] == {"ndcg_at_8": 0.65, "hit_at_5": 0.9, "p95_s": 1.2}
    assert len(document["prod_p95_s"]) == 3
    queries = document["queries"]
    assert [query["id"] for query in queries] == [f"h4-{number:02d}" for number in range(1, len(queries) + 1)]
    assert len(queries) >= 32
    for query in queries:
        assert query["gold"]
        assert 1 <= len(query["top5"]) <= 5
        assert all(isinstance(chunk_id, str) and chunk_id for chunk_id in query["gold"])
        assert all(isinstance(chunk_id, str) and chunk_id for chunk_id in query["top5"])
    assert set(document["slices"]) >= {"all", *(query["family"] for query in queries)}
    assert document["hit_at_5"] == document["slices"]["all"]["hit_at_5"]
    assert document["ndcg_at_8"] == document["slices"]["all"]["ndcg_at_8"]


def test_second_holdout4_measure_is_refused(tmp_path: Path) -> None:
    target = tmp_path / MEASURE
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="повторный замер"):
        measure_holdout4_once(target)
