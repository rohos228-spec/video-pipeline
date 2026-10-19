"""Holdout-5 заморожен до подбора. Retrieve и метрики в тесте нет."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.orchestrator.rag.store import chunk_id_for
from evals.orchestrator.dev_v2 import load_dev100
from evals.orchestrator.frozen_datasets import FROZEN_SHA256, file_sha256
from evals.orchestrator.holdout import HOLDOUT5_NAME, HoldoutLeakError, _index_docs, is_holdout_id, read_for_tuning
from evals.orchestrator.holdout5 import (
    HOLDOUT5_PATH,
    MIN_QUERIES,
    build_holdout5_document,
    load_holdout5_queries,
    write_holdout5_file,
)
from tests.rag.test_holdout4_guard import _catalog_identity_phrases
from tests.rag.test_holdout_guard import _jaccard, _stems

DATA = Path("evals/orchestrator/datasets")


def _norm(text: str) -> str:
    folded = text.lower().replace("ё", "е")
    return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", folded).split())


def _blocked_queries() -> list[dict]:
    """Dev-v2 и holdout 1–4. Артефакт замера holdout-4 не читается."""
    dev = load_dev100()
    hold3 = json.loads((DATA / "precision_live_holdout3.json").read_text(encoding="utf-8"))["queries"]
    hold4 = json.loads((DATA / "precision_live_holdout4.json").read_text(encoding="utf-8"))["queries"]
    return [*dev, *hold3, *hold4]


def test_holdout5_is_frozen_and_tuning_cannot_load_it() -> None:
    pin = FROZEN_SHA256[HOLDOUT5_NAME]
    assert file_sha256(HOLDOUT5_PATH) == pin["sha256"]
    assert pin["sha256"] == "061ccf3fe9a2a57c89fa88d89c07d0842582bfefc9aeed8c522aecf7f6567849"
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(HOLDOUT5_PATH)
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(Path("/tmp") / HOLDOUT5_NAME)
    source = Path("evals/orchestrator/precision_live.py").read_text(encoding="utf-8")
    tune = source[source.index("def run_tune"): source.index("def contour_c_acceptance")]
    assert "load_holdout5" not in tune
    assert "holdout5" not in tune
    assert "precision_live_holdout5" not in tune
    queries = load_holdout5_queries()
    assert len(queries) >= MIN_QUERIES
    assert [query["id"] for query in queries] == [f"h5-{number:02d}" for number in range(1, len(queries) + 1)]
    assert is_holdout_id("h5-01") is True
    counts: dict[str, int] = {}
    for query in queries:
        counts[query["family"]] = counts.get(query["family"], 0) + 1
    for family in ("camera", "world", "characters"):
        assert counts[family] >= 6
    assert build_holdout5_document()["queries"] == queries
    before = HOLDOUT5_PATH.read_bytes()
    write_holdout5_file()
    assert HOLDOUT5_PATH.read_bytes() == before
    with pytest.raises(Exception):
        write_holdout5_file(rewrite=True)


def test_holdout5_gold_is_the_answering_chunks_only() -> None:
    document = json.loads(HOLDOUT5_PATH.read_text(encoding="utf-8"))
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


def test_holdout5_texts_do_not_overlap_dev_v2_or_holdouts_1_to_4() -> None:
    prior = _blocked_queries()
    prior_norm = [_norm(query["text"]) for query in prior]
    prior_stems = [_stems(query["text"]) for query in prior]
    fresh = json.loads(HOLDOUT5_PATH.read_text(encoding="utf-8"))["queries"]
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
