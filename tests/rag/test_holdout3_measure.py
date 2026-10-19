"""Один замер holdout-3 привязан к замороженному конфигу. Повторный прогон запрещён."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.orchestrator.frozen_datasets import FROZEN_SHA256, file_sha256
from evals.orchestrator.precision_live import measure_holdout3_once

DATA = Path("evals/orchestrator/datasets")
MEASURE = "precision_live_holdout3_measure.json"


def test_holdout3_measure_pin_and_config_match_1627ff1e() -> None:
    path = DATA / MEASURE
    pin = FROZEN_SHA256[MEASURE]
    assert pin["commit"] == "1627ff1e"
    assert file_sha256(path) == pin["sha256"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["config_commit"] == "1627ff1edd9fa4ca87f807dd757d7bc37e2372aa"
    assert document["query_sha256"] == FROZEN_SHA256["precision_live_holdout3.json"]["sha256"]
    assert document["passes"] == 1
    assert document["config"]["aggregate"] == "expand_tail"
    assert document["config"]["rrf_k"] == 20
    assert (document["config"]["fusion_bm25"], document["config"]["fusion_rerank"]) == (0.25, 1.5)
    assert document["verdict_label"] == "предварительный, на фрагментных запросах"
    assert document["thresholds"] == {"ndcg_at_8": 0.65, "hit_at_5": 0.9, "p95_s": 1.2}


def test_second_holdout3_measure_is_refused(tmp_path: Path) -> None:
    target = tmp_path / MEASURE
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="повторный замер"):
        measure_holdout3_once(target)
