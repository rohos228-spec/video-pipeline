"""Holdout-6 заморожен до правок структуры. Retrieve и метрики в тесте нет."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.orchestrator.rag.store import chunk_id_for
from evals.orchestrator.dev_v2 import load_dev100
from evals.orchestrator.frozen_datasets import FROZEN_SHA256, file_sha256
from evals.orchestrator.holdout import HOLDOUT6_NAME, HoldoutLeakError, _index_docs, is_holdout_id, read_for_tuning
from evals.orchestrator.holdout6 import (
    HOLDOUT6_PATH,
    MIN_QUERIES,
    WEIGHTED,
    build_holdout6_document,
    load_holdout6_queries,
    write_holdout6_file,
)
from tests.rag.test_holdout4_guard import _catalog_identity_phrases
from tests.rag.test_holdout_guard import _jaccard, _stems

DATA = Path("evals/orchestrator/datasets")


def _norm(text: str) -> str:
    folded = text.lower().replace("ё", "е")
    return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", folded).split())


def _blocked_queries() -> list[dict]:
    """Dev-v2 и holdout 1–5. Артефакты замеров не читаются."""
    dev = load_dev100()
    hold3 = json.loads((DATA / "precision_live_holdout3.json").read_text(encoding="utf-8"))["queries"]
    hold4 = json.loads((DATA / "precision_live_holdout4.json").read_text(encoding="utf-8"))["queries"]
    hold5 = json.loads((DATA / "precision_live_holdout5.json").read_text(encoding="utf-8"))["queries"]
    return [*dev, *hold3, *hold4, *hold5]


def test_holdout6_is_frozen_and_tuning_cannot_load_it() -> None:
    pin = FROZEN_SHA256[HOLDOUT6_NAME]
    assert file_sha256(HOLDOUT6_PATH) == pin["sha256"]
    assert pin["commit"] == "pending"
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(HOLDOUT6_PATH)
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(Path("/tmp") / HOLDOUT6_NAME)
    source = Path("evals/orchestrator/precision_live.py").read_text(encoding="utf-8")
    tune = source[source.index("def run_tune"): source.index("def contour_c_acceptance")]
    assert "load_holdout6" not in tune
    assert "holdout6" not in tune
    assert "precision_live_holdout6" not in tune
    queries = load_holdout6_queries()
    assert len(queries) >= MIN_QUERIES
    assert [query["id"] for query in queries] == [f"h6-{number:02d}" for number in range(1, len(queries) + 1)]
    assert is_holdout_id("h6-01") is True
    counts: dict[str, int] = {}
    for query in queries:
        counts[query["family"]] = counts.get(query["family"], 0) + 1
    assert set(counts) == {
        "camera",
        "world",
        "characters",
        "action",
        "assemble",
        "shots",
        "script",
        "skeleton",
        "rules",
    }
    ceiling = max(count for family, count in counts.items() if family not in WEIGHTED)
    for family in WEIGHTED:
        assert counts[family] >= 6
        assert counts[family] > ceiling
    assert build_holdout6_document()["queries"] == queries
    before = HOLDOUT6_PATH.read_bytes()
    write_holdout6_file()
    assert HOLDOUT6_PATH.read_bytes() == before
    with pytest.raises(Exception):
        write_holdout6_file(rewrite=True)


def test_holdout6_gold_is_the_answering_chunks_only() -> None:
    document = json.loads(HOLDOUT6_PATH.read_text(encoding="utf-8"))
    by_source: dict[str, set[str]] = {}
    for doc in _index_docs():
        by_source.setdefault(doc.source_id, set()).add(doc.part_key)
    for query in document["queries"]:
        justification = query["justification"]
        assert justification.strip() == justification
        assert justification and "\n" not in justification
        assert query["relevant"]
        got = set()
        for item in query["relevant"]:
            assert item["chunk_id"] == chunk_id_for(item["corpus"], item["source_id"], item["part_key"])
            assert item["grade"] == 1
            got.add((item["source_id"], item["part_key"]))
        universe = {
            (source_id, part_key)
            for source_id in {item["source_id"] for item in query["relevant"]}
            for part_key in by_source[source_id]
        }
        assert got < universe
        if query["family"] == "rules":
            assert query["kind"] == "rule"
            assert len(query["relevant"]) == 1
            assert query["relevant"][0]["part_key"].startswith("rule-")


def test_holdout6_texts_do_not_overlap_dev_v2_or_holdouts_1_to_5() -> None:
    prior = _blocked_queries()
    prior_norm = [_norm(query["text"]) for query in prior]
    prior_stems = [_stems(query["text"]) for query in prior]
    fresh = json.loads(HOLDOUT6_PATH.read_text(encoding="utf-8"))["queries"]
    phrases = _catalog_identity_phrases()
    seen: set[str] = set()
    for query in fresh:
        folded = _norm(query["text"])
        assert folded not in seen
        seen.add(folded)
        assert folded not in prior_norm
        stems = _stems(query["text"])
        nearest = max(_jaccard(stems, previous) for previous in prior_stems)
        assert nearest < 0.34, (query["id"], nearest)
        text = query["text"].lower().replace("ё", "е")
        leaked = [phrase for phrase in phrases if phrase in text]
        assert not leaked, (query["id"], leaked)
        assert re.search(r"правил\w*\s*\d+", text) is None
        assert re.search(r"rule\s*-?\s*\d+", text) is None
