"""Сплит dev/test зафиксирован и не зависит от меток."""

from __future__ import annotations

import json

from evals.orchestrator.query_split import QUERY_PATH, SPLIT_PATH, half_of, split_queries


def test_split_is_odd_even_and_committed() -> None:
    queries = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    live_29 = next(query for query in queries if query["id"] == "live-29")
    groups = split_queries(queries)
    committed = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    assert committed["dev"] == [query["id"] for query in groups["dev"]]
    assert committed["test"] == [query["id"] for query in groups["test"]]
    assert len(committed["dev"]) == 25
    assert len(committed["test"]) == 25
    assert set(committed["dev"]).isdisjoint(committed["test"])
    assert half_of("live-01") == "dev"
    assert half_of("live-02") == "test"
    assert half_of("live-29") == "dev"
    again = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    assert next(query for query in again if query["id"] == "live-29") == live_29
