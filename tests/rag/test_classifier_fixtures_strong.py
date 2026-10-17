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


# Эти четыре файла в 3193062f сменили только recorded_model. Тела остались скриптом.
_SCRIPTED_BODIES = (
    "96077882a73d40e956c71f6e235953ac271fb1f2ee46f747bf0e918fc51dff98.json",
    "afcdef2630a81a51cc52b2bd8d2668e3530898763cd947f605d2b8cbcefa4a51.json",
    "bc64319b6cb5d2c5331a442c885a6c5edd8b59e28032aeb3653763141e1d49b9.json",
    "ee364cd00bd57ef9c6a7eea876013a5d1d9ef557b4958818f69e7cba1ca05965.json",
)


def test_scripted_bodies_are_not_labeled_as_a_live_model() -> None:
    for name in _SCRIPTED_BODIES:
        payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
        assert payload["recorded_model"] == "scripted"
        assert payload["recorded_at"] == "2026-10-17T00:00:00Z"
        assert payload["response"]["provider"] == "scripted"
        assert str(payload["response"]["call_id"]).startswith("script-")


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
