"""Один замер holdout-5 на eba4e90e и дефолте 5ce57af9. Повтор запрещён."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.orchestrator.frozen_datasets import FROZEN_SHA256, file_sha256
from evals.orchestrator.holdout import HOLDOUT5_MEASURE_NAME, HoldoutLeakError, read_for_tuning
from evals.orchestrator.precision_live import measure_holdout5_once

DATA = Path("evals/orchestrator/datasets")
MEASURE = "precision_live_holdout5_measure.json"


def test_holdout5_measure_pin_cites_dataset_and_config() -> None:
    path = DATA / MEASURE
    pin = FROZEN_SHA256[MEASURE]
    assert pin["commit"] == "bbec0dab"
    assert pin["config_commit"] == "5ce57af9"
    assert file_sha256(path) == pin["sha256"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["commit"] == "eba4e90ec8fe0e0afb9a0dd597a50dc92003d1a1"
    assert document["dataset_commit"] == "bbec0dab8db105d86dc1c1eb4b44d6f0100e011e"
    assert document["config_commit"] == "5ce57af9ad80da74be40b6a78e685a116e9735f4"
    assert document["query_sha256"] == FROZEN_SHA256["precision_live_holdout5.json"]["sha256"]
    assert document["passes"] == 1
    assert document["config"]["aggregate"] == "expand_tail"
    assert document["config"]["rrf_k"] == 20
    assert (document["config"]["fusion_bm25"], document["config"]["fusion_rerank"]) == (0.25, 1.5)
    assert document["config"]["bm25_protect"] == 2.0
    assert document["thresholds"] == {"ndcg_at_8": 0.65, "hit_at_5": 0.9, "p95_s": 1.2}
    assert len(document["prod_p95_s"]) == 3
    queries = document["queries"]
    assert [query["id"] for query in queries] == [f"h5-{number:02d}" for number in range(1, 41)]
    for query in queries:
        assert query["gold"]
        assert 1 <= len(query["top5"]) <= 5
        assert all(isinstance(chunk_id, str) and chunk_id for chunk_id in query["gold"])
        assert all(isinstance(chunk_id, str) and chunk_id for chunk_id in query["top5"])
    assert set(document["slices"]) >= {"all", *(query["family"] for query in queries)}
    assert document["hit_at_5"] == document["slices"]["all"]["hit_at_5"]
    assert document["ndcg_at_8"] == document["slices"]["all"]["ndcg_at_8"]
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(path)
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(Path("/tmp") / HOLDOUT5_MEASURE_NAME)


def test_second_holdout5_measure_is_refused(tmp_path: Path) -> None:
    target = tmp_path / MEASURE
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="повторный замер"):
        measure_holdout5_once(target)
