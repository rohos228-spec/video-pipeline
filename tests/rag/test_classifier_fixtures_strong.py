"""Классификатор в фикстурах идёт strong с reasoning_effort high.

D6 (fast-tier без high) в правиле 2 не подтверждён на kick-off, поэтому
ключ replay — strong + high, а не fast + null.
"""

from __future__ import annotations

import json

from app.orchestrator.rag.llm_fixtures import ReplayRouter
from evals.orchestrator.classifier import classifier_messages
from evals.orchestrator.e6_dataset import e6_queries
from evals.orchestrator.golden import FIXTURE_DIR


def test_every_fixture_call_is_strong_with_high_reasoning() -> None:
    files = list(FIXTURE_DIR.glob("*.json"))
    assert files
    for path in files:
        meta = json.loads(path.read_text(encoding="utf-8"))["request_meta"]
        assert meta["tier"] == "strong"
        assert meta["reasoning_effort"] == "high"


def test_classifier_replay_uses_strong_high() -> None:
    router = ReplayRouter(FIXTURE_DIR)
    played = 0
    for row in e6_queries():
        if row["deterministic"]:
            continue
        response = router.call("strong", classifier_messages(row["text"]), False, reasoning_effort="high")
        assert response.tier == "strong"
        played += 1
    assert played == 8
